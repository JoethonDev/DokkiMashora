"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.core.forms import StyledFormMixin
from apps.students.identity import (
    country_code_field_choices,
    identity_label,
    normalize_country_code,
    validate_unique_identity_values,
)
from apps.students.models import StudentProfile

from .constants import (
    ACTIVE_ROLE_GROUPS,
    GROUP_STUDENT,
    STAFF_ROLE_GROUPS,
)
from .models import User
from .services import lock_identity_writes


class EmailAuthenticationForm(StyledFormMixin, AuthenticationForm):
    """Authenticate using email rather than username."""

    username = forms.EmailField(
        label=_("Email"),
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class ProfileForm(StyledFormMixin, forms.Form):
    """Edit the current user's own identity and contact fields.

    Phone is single-owner: for a student it lives on StudentProfile.phone only;
    for a staff user (no profile) it lives on User.phone. Email remains on
    User only and is not editable here.
    """

    full_name = forms.CharField(label=_("Full name"), max_length=150, widget=forms.TextInput())
    full_name_ar = forms.CharField(
        label=_("Full name (Arabic)"), max_length=150, required=False, widget=forms.TextInput()
    )
    phone = forms.CharField(label=_("Phone number"), max_length=32, widget=forms.TextInput())

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            raise ValidationError(_("Phone number is required."))
        profile = getattr(self.user, "student_profile", None)
        normalized_phone, _identity = validate_unique_identity_values(
            country_code=profile.country_code if profile else self.user.country_code,
            phone=phone,
            user_id=self.user.pk,
            profile_id=profile.pk if profile else None,
        )
        return normalized_phone


class ActivationConfirmForm(StyledFormMixin, forms.Form):
    """Set the initial password during the one-time account activation step.

    The activation link is the one-time token; the initial password is a
    random secret that was never delivered, so it is established here with the
    full Django password validators before the user reaches any dashboard.
    """

    new_password1 = forms.CharField(
        label=_("New password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label=_("Confirm password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", _("The two password fields didn't match."))
        return cleaned

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1", "")
        if password:
            validate_password(password, user=self.user)
        return password


class AdminUserCreationForm(StyledFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": _("Email")}


class AdminUserChangeForm(StyledFormMixin, UserChangeForm):
    class Meta:
        model = User
        fields = (
            "email",
            "full_name",
            "full_name_ar",
            "phone",
            "country_code",
            "national_id",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and getattr(self.instance, "student_profile", None) is not None:
            # Student identity belongs to StudentProfile; editing duplicate User
            # fields here could desynchronize the two representations.
            for field_name in ("full_name", "phone", "country_code", "national_id"):
                self.fields.pop(field_name, None)
        if not self.is_bound and self.instance.pk and not self.instance.full_name_ar:
            self.initial["full_name_ar"] = self.instance.full_name

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email).exists():
            raise ValidationError(_("This email is already in use."))
        return email

    def clean_country_code(self):
        return normalize_country_code(self.cleaned_data.get("country_code", ""))

    def clean_phone(self):
        raw_phone = (self.cleaned_data.get("phone") or "").strip()
        if not raw_phone:
            return ""
        profile = getattr(self.instance, "student_profile", None)
        normalized_phone, _identity = validate_unique_identity_values(
            country_code=self.cleaned_data.get("country_code") or self.instance.country_code,
            phone=raw_phone,
            user_id=self.instance.pk,
            profile_id=profile.pk if profile else None,
        )
        return normalized_phone

    def clean_national_id(self):
        raw_identity = (self.cleaned_data.get("national_id") or "").strip()
        if not raw_identity:
            return ""
        profile = getattr(self.instance, "student_profile", None)
        _phone, normalized_identity = validate_unique_identity_values(
            country_code=self.cleaned_data.get("country_code") or self.instance.country_code,
            identity_number=raw_identity,
            user_id=self.instance.pk,
            profile_id=profile.pk if profile else None,
        )
        return normalized_identity


class UserEditForm(StyledFormMixin, forms.ModelForm):
    email = forms.EmailField(label=_("Email"), widget=forms.EmailInput())
    full_name = forms.CharField(
        label=_("Full name"), max_length=150, required=False, widget=forms.TextInput()
    )
    full_name_ar = forms.CharField(
        label=_("Full name (Arabic)"), max_length=150, required=False, widget=forms.TextInput()
    )
    phone = forms.CharField(label=_("Phone number"), max_length=32, required=False, widget=forms.TextInput())
    country_code = forms.ChoiceField(
        label=_("Country"),
        choices=[],
        required=False,
        widget=forms.Select(),
    )
    national_id = forms.CharField(
        label=_("National ID"), max_length=32, required=False, widget=forms.TextInput()
    )
    is_active = forms.BooleanField(label=_("Account active"), required=False)
    date_of_birth = forms.DateField(
        label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    gender = forms.ChoiceField(
        label=_("Gender"),
        choices=StudentProfile._meta.get_field("gender").choices,
        required=False,
        widget=forms.Select(),
    )
    address = forms.CharField(label=_("Address"), required=False, widget=forms.TextInput())
    education = forms.ChoiceField(
        label=_("Education"),
        choices=StudentProfile._meta.get_field("education").choices,
        required=False,
        widget=forms.Select(),
    )
    specialization = forms.CharField(label=_("Specialization"), required=False, widget=forms.TextInput())
    employer = forms.CharField(label=_("Employer"), required=False, widget=forms.TextInput())
    current_job = forms.CharField(label=_("Current job"), required=False, widget=forms.TextInput())
    church = forms.CharField(label=_("Church"), required=False, widget=forms.TextInput())
    priest_name = forms.CharField(label=_("Priest name"), required=False, widget=forms.TextInput())
    priest_contact = forms.CharField(label=_("Priest contact"), required=False, widget=forms.TextInput())
    new_password = forms.CharField(
        label=_("New password"), required=False, strip=False, widget=forms.PasswordInput()
    )
    role = forms.ChoiceField(
        label=_("Role"),
        choices=[(role, _(role)) for role in ACTIVE_ROLE_GROUPS],
        required=False,
        widget=forms.Select(),
    )

    class Meta:
        model = User
        fields = ("email", "full_name", "full_name_ar", "phone", "country_code", "national_id", "is_active")

    STUDENT_ONLY_FIELDS = [
        "country_code",
        "national_id",
        "date_of_birth",
        "gender",
        "address",
        "education",
        "specialization",
        "employer",
        "current_job",
        "church",
        "priest_name",
        "priest_contact",
    ]

    def __init__(self, *args, **kwargs):
        staff_only = kwargs.pop("staff_only", False)
        super().__init__(*args, **kwargs)
        self.staff_only = staff_only
        role_choices = ACTIVE_ROLE_GROUPS
        self.fields["country_code"].choices = country_code_field_choices(
            self.instance.country_code if self.instance.pk else ""
        )
        existing_staff_roles = (
            set(self.instance.groups.values_list("name", flat=True)).intersection(STAFF_ROLE_GROUPS)
            if self.instance.pk
            else set()
        )
        if staff_only:
            self.fields["role"].choices = [(role, _(role)) for role in role_choices if role != GROUP_STUDENT]
            for fname in self.STUDENT_ONLY_FIELDS:
                if fname in self.fields:
                    del self.fields[fname]
            self._allowed_roles = tuple(role for role in role_choices if role != GROUP_STUDENT)
        else:
            if existing_staff_roles:
                self.fields["role"].choices = [(role, _(role)) for role in role_choices]
                self._allowed_roles = role_choices
            else:
                self.fields["role"].choices = [(GROUP_STUDENT, _(GROUP_STUDENT))]
                self._allowed_roles = (GROUP_STUDENT,)
        profile = getattr(self.instance, "student_profile", None)
        if not self.instance.pk:
            self.fields["new_password"].required = True
            self.fields["role"].required = True
        else:
            self.initial["role"] = self.instance.primary_role or (
                GROUP_STUDENT if profile is not None else ""
            )
            self.fields["role"].required = True
            if not staff_only and not existing_staff_roles:
                self.fields["role"].disabled = True
        role = self.data.get("role") or self.initial.get("role")
        if role == GROUP_STUDENT:
            for name in (
                "full_name",
                "phone",
                "country_code",
                "national_id",
                "date_of_birth",
                "gender",
                "address",
                "education",
            ):
                if name in self.fields:
                    self.fields[name].required = True
        if profile is not None:
            # StudentProfile is the single owner of these identity fields (a
            # student's User.phone is deliberately cleared on save). They must
            # go through the form's `initial` dict: assigning `field.initial`
            # has no effect for names the model instance also supplies,
            # because `self.initial` (built from the instance) wins.
            self.initial.update(
                {
                    "full_name": profile.full_name,
                    "phone": profile.phone,
                    "national_id": profile.national_id,
                    "date_of_birth": profile.date_of_birth,
                    "gender": profile.gender,
                    "address": profile.address,
                    "education": profile.education,
                    "specialization": profile.specialization,
                    "employer": profile.employer,
                    "current_job": profile.current_job,
                    "church": profile.church,
                    "priest_name": profile.priest_name,
                    "priest_contact": profile.priest_contact,
                }
            )
        if not self.is_bound and not self.initial.get("full_name_ar"):
            self.initial["full_name_ar"] = self.initial.get("full_name", "")

    def clean_role(self):
        role = self.cleaned_data.get("role")
        if role not in self._allowed_roles:
            raise ValidationError(_("Select a supported role for this account."))
        return role

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            raise ValidationError(_("Phone number is required."))
        profile = getattr(self.instance, "student_profile", None)
        normalized_phone, _identity = validate_unique_identity_values(
            country_code=self.cleaned_data.get("country_code") or self.instance.country_code,
            phone=phone,
            user_id=self.instance.pk,
            profile_id=profile.pk if profile else None,
        )
        return normalized_phone

    def clean_national_id(self):
        raw_id = (self.cleaned_data.get("national_id") or "").strip()
        profile = getattr(self.instance, "student_profile", None)
        cc_raw = self.cleaned_data.get("country_code", "")
        country_code = normalize_country_code(cc_raw or self.instance.country_code or "")

        if profile is not None and not raw_id:
            label = identity_label(country_code)
            raise ValidationError(_("%(label)s is required.") % {"label": label})
        if not raw_id:
            return raw_id
        profile_pk = profile.pk if profile is not None else None
        _phone, normalized_id = validate_unique_identity_values(
            country_code=country_code,
            identity_number=raw_id,
            user_id=self.instance.pk,
            profile_id=profile_pk,
        )
        return normalized_id

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        qs = User.objects.exclude(pk=self.instance.pk).filter(email__iexact=email)
        if qs.exists():
            raise ValidationError(_("This email is already in use."))
        return email

    def clean_new_password(self):
        password = self.cleaned_data.get("new_password") or ""
        if password:
            validate_password(password, user=self.instance)
        return password

    def clean_country_code(self):
        cc = self.cleaned_data.get("country_code", "")
        profile = getattr(self.instance, "student_profile", None)
        if not cc and profile is not None:
            raise ValidationError(_("Country is required for student users."))
        if cc:
            return normalize_country_code(cc)
        return cc

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        profile = getattr(user, "student_profile", None)
        data = self.cleaned_data
        existing_groups = list(user.groups.all()) if user.pk else []
        user.email = data["email"]
        user.full_name = data.get("full_name", "")
        user.full_name_ar = data.get("full_name_ar") or user.full_name
        # Phone is single-owner: staff keep it on User.phone; a student's phone
        # lives on StudentProfile.phone only, and any stale User.phone copy is
        # cleared here so the mirror never re-appears on edits.
        user.phone = data.get("phone", "") if self.staff_only else ""
        user.country_code = data.get("country_code", user.country_code)
        # Staff forms intentionally omit student-only identity fields; do not
        # clear a legacy value merely because that field was not rendered.
        user.national_id = data.get("national_id", user.national_id)
        user.is_active = bool(data.get("is_active"))
        role = data["role"]
        user.is_staff = bool(user.is_superuser or role in STAFF_ROLE_GROUPS)
        if data.get("new_password"):
            user.set_password(data["new_password"])
        if commit:
            # User and StudentProfile identity fields live in separate tables;
            # serialize the cross-table write before the database constraints
            # provide their final race-time protection.
            lock_identity_writes()
            email = (data["email"] or "").strip().lower()
            if User.objects.exclude(pk=user.pk).filter(email__iexact=email).exists():
                raise ValidationError({"email": _("This email is already in use.")})
            try:
                data["phone"], _identity = validate_unique_identity_values(
                    country_code=data.get("country_code") or user.country_code,
                    phone=data.get("phone", ""),
                    user_id=user.pk,
                    profile_id=profile.pk if profile else None,
                )
            except ValidationError as exc:
                raise ValidationError({"phone": exc}) from exc
            try:
                _phone, data["national_id"] = validate_unique_identity_values(
                    country_code=data.get("country_code") or user.country_code,
                    identity_number=data.get("national_id", ""),
                    user_id=user.pk,
                    profile_id=profile.pk if profile else None,
                )
            except ValidationError as exc:
                raise ValidationError({"national_id": exc}) from exc
            data["email"] = email
            user.email = email
            user.save()
            if profile is not None:
                profile.full_name = data.get("full_name", profile.full_name)
                profile.date_of_birth = data.get("date_of_birth") or profile.date_of_birth
                profile.gender = data.get("gender", profile.gender)
                profile.address = data.get("address", profile.address)
                profile.phone = data.get("phone", profile.phone)
                profile.country_code = data.get("country_code", profile.country_code)
                profile.national_id = data.get("national_id", profile.national_id)
                profile.education = data.get("education", profile.education)
                profile.specialization = data.get("specialization", profile.specialization)
                profile.employer = data.get("employer", profile.employer)
                profile.current_job = data.get("current_job", profile.current_job)
                profile.church = data.get("church", profile.church)
                profile.priest_name = data.get("priest_name", profile.priest_name)
                profile.priest_contact = data.get("priest_contact", profile.priest_contact)
                profile.save()
            elif role == GROUP_STUDENT:
                StudentProfile.objects.create(
                    user=user,
                    full_name=data.get("full_name", ""),
                    date_of_birth=data["date_of_birth"],
                    gender=data["gender"],
                    address=data["address"],
                    phone=data["phone"],
                    country_code=data.get("country_code") or "EG",
                    national_id=data["national_id"],
                    education=data["education"],
                    specialization=data.get("specialization", ""),
                    employer=data.get("employer", ""),
                    current_job=data.get("current_job", ""),
                    church=data.get("church", ""),
                    priest_name=data.get("priest_name", ""),
                    priest_contact=data.get("priest_contact", ""),
                )
            retained_groups = [group for group in existing_groups if group.name not in ACTIVE_ROLE_GROUPS]
            user.groups.set([*retained_groups, Group.objects.get(name=role)])
            if hasattr(user, "_group_names_cache"):
                delattr(user, "_group_names_cache")
        return user


class UserUploadForm(StyledFormMixin, forms.Form):
    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("CSV: email, full_name"),
        widget=forms.FileInput(attrs={"accept": ".csv"}),
    )

    def clean_csv_file(self):
        file = self.cleaned_data.get("csv_file")
        if not file:
            raise ValidationError(_("No file provided"))
        if not file.name.endswith(".csv"):
            raise ValidationError(_("Only CSV files are accepted"))
        if file.size > 10 * 1024 * 1024:
            raise ValidationError(_("File size exceeds the maximum of 10 MB"))
        if file.size == 0:
            raise ValidationError(_("Uploaded file is empty"))
        return file
