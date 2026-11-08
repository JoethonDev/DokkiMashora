"""Set-based student identity matching for CSV imports.

Both the payment import and the historical application import resolve
students by national ID, email, or phone. Keeping this in one place ensures
the same ambiguity and conflict rules apply to every bulk import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.accounts.constants import (
    MATCH_METHOD_AMBIGUOUS,
    MATCH_METHOD_EMAIL,
    MATCH_METHOD_NATIONAL_ID,
    MATCH_METHOD_NONE,
    MATCH_METHOD_PHONE,
)
from apps.core.phone import normalize_phone
from apps.students.identity import normalize_identity_number
from apps.students.models import StudentProfile


@dataclass(frozen=True)
class MatchError:
    """A translatable matching error that can be stored and rendered later.

    Import previews persist the message id and parameters instead of the
    language-specific string, so another admin can read the error in their
    own active language.
    """

    msgid: str
    params: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        message = _(self.msgid)
        return message % self.params if self.params else message

    def as_dict(self) -> dict[str, object]:
        return {"msgid": self.msgid, "params": self.params}


def normalize_national_id(value: str) -> str:
    """Normalize an identity value for CSV matching (EG default)."""
    return normalize_identity_number("EG", value)


def normalize_national_id_for_matching(value: str) -> list[str]:
    """Return normalized identity variants to try when matching.

    Uses country-aware normalization (default EG). For passport IDs, an
    uppercased variant is also returned.
    """
    stripped = normalize_national_id(value)
    variants = [stripped]
    if any(c.isalpha() for c in stripped):
        variants.append(normalize_identity_number("EG", stripped.upper()))
    return variants


def match_student(row: dict[str, str]) -> tuple[StudentProfile | None, str, MatchError | None]:
    """Resolve one CSV row to a student identity, or return an error."""
    identity_variants = [
        item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
    ]
    email = row.get("email", "").strip().lower()
    phone = normalize_phone(row.get("phone", ""))
    candidates: dict[int, StudentProfile] = {}
    sources: dict[int, str] = {}

    if identity_variants:
        qs = StudentProfile.objects.filter(national_id=identity_variants[0])
        if qs.count() == 1:
            student = qs.first()
            candidates[student.id] = student
            sources[student.id] = MATCH_METHOD_NATIONAL_ID
        elif qs.count() > 1:
            return (
                None,
                MATCH_METHOD_AMBIGUOUS,
                MatchError(
                    gettext_noop("Multiple students match national ID %(value)s"),
                    {"value": identity_variants[0]},
                ),
            )
        elif len(identity_variants) > 1:
            qs = StudentProfile.objects.filter(national_id=identity_variants[1])
            if qs.count() == 1:
                student = qs.first()
                candidates[student.id] = student
                sources[student.id] = MATCH_METHOD_NATIONAL_ID
            elif qs.count() > 1:
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    MatchError(
                        gettext_noop("Multiple students match national ID %(value)s"),
                        {"value": identity_variants[0]},
                    ),
                )

    if email:
        qs = StudentProfile.objects.filter(user__email__iexact=email)
        if qs.count() == 1:
            student = qs.first()
            candidates[student.id] = student
            sources[student.id] = sources.get(student.id, "") + "+" + MATCH_METHOD_EMAIL
        elif qs.count() > 1:
            return (
                None,
                MATCH_METHOD_AMBIGUOUS,
                MatchError(
                    gettext_noop("Multiple students match email %(value)s"),
                    {"value": email},
                ),
            )

    if phone:
        qs = StudentProfile.objects.filter(phone=phone)
        if qs.count() == 1:
            student = qs.first()
            candidates[student.id] = student
            sources[student.id] = sources.get(student.id, "") + "+" + MATCH_METHOD_PHONE
        elif qs.count() > 1:
            return (
                None,
                MATCH_METHOD_AMBIGUOUS,
                MatchError(
                    gettext_noop("Multiple students match phone %(value)s"),
                    {"value": phone},
                ),
            )

    if not candidates:
        return (
            None,
            MATCH_METHOD_NONE,
            MatchError(gettext_noop("No student found matching any identifier")),
        )
    if len(candidates) > 1:
        return (
            None,
            MATCH_METHOD_AMBIGUOUS,
            MatchError(
                gettext_noop("Conflicting identifiers match %(count)d different students"),
                {"count": len(candidates)},
            ),
        )

    student = next(iter(candidates.values()))
    method_string = sources.get(student.id, "")
    if MATCH_METHOD_NATIONAL_ID in method_string:
        method = MATCH_METHOD_NATIONAL_ID
    elif MATCH_METHOD_EMAIL in method_string:
        method = MATCH_METHOD_EMAIL
    elif MATCH_METHOD_PHONE in method_string:
        method = MATCH_METHOD_PHONE
    else:
        method = MATCH_METHOD_NONE
    return student, method, None


def build_student_matcher(
    rows: list[dict[str, str]],
) -> Callable[[dict[str, str]], tuple[StudentProfile | None, str, MatchError | None]]:
    """Build one set-based identifier index for a bounded CSV batch."""
    identities: set[str] = set()
    emails: set[str] = set()
    phones: set[str] = set()
    for row in rows:
        identities.update(
            item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
        )
        email = row.get("email", "").strip().lower()
        phone = normalize_phone(row.get("phone", ""))
        if email:
            emails.add(email)
        if phone:
            phones.add(phone)

    candidates = (
        StudentProfile.objects.select_related("user")
        .annotate(normalized_email_db=Lower("user__email"))
        .filter(Q(national_id__in=identities) | Q(normalized_email_db__in=emails) | Q(phone__in=phones))
    )
    by_identity: dict[str, list[StudentProfile]] = {}
    by_email: dict[str, list[StudentProfile]] = {}
    by_phone: dict[str, list[StudentProfile]] = {}
    for student in candidates:
        if student.national_id:
            by_identity.setdefault(student.national_id, []).append(student)
        email = (student.user.email or "").strip().lower()
        if email:
            by_email.setdefault(email, []).append(student)
        if student.phone:
            by_phone.setdefault(normalize_phone(student.phone), []).append(student)

    def resolve(row: dict[str, str]) -> tuple[StudentProfile | None, str, MatchError | None]:
        matches: dict[int, StudentProfile] = {}
        methods: dict[int, str] = {}
        identity_values = [
            item for item in normalize_national_id_for_matching(row.get("national_id", "")) if item
        ]
        email = row.get("email", "").strip().lower()
        phone = normalize_phone(row.get("phone", ""))

        for value in identity_values:
            students = by_identity.get(value, [])
            if len(students) > 1:
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    MatchError(
                        gettext_noop("Multiple students match national ID %(value)s"),
                        {"value": value},
                    ),
                )
            if students:
                matches[students[0].pk] = students[0]
                methods[students[0].pk] = MATCH_METHOD_NATIONAL_ID

        for value, index, method in (
            (email, by_email, MATCH_METHOD_EMAIL),
            (phone, by_phone, MATCH_METHOD_PHONE),
        ):
            if not value:
                continue
            students = index.get(value, [])
            if len(students) > 1:
                msgid = (
                    gettext_noop("Multiple students match email %(value)s")
                    if method == MATCH_METHOD_EMAIL
                    else gettext_noop("Multiple students match phone %(value)s")
                )
                return (
                    None,
                    MATCH_METHOD_AMBIGUOUS,
                    MatchError(msgid, {"value": value}),
                )
            if students:
                matches[students[0].pk] = students[0]
                methods.setdefault(students[0].pk, method)

        if not matches:
            return (
                None,
                MATCH_METHOD_NONE,
                MatchError(gettext_noop("No student found matching any identifier")),
            )
        if len(matches) > 1:
            return (
                None,
                MATCH_METHOD_AMBIGUOUS,
                MatchError(gettext_noop("Conflicting identifiers match different students")),
            )
        student = next(iter(matches.values()))
        return student, methods[student.pk], None

    return resolve
