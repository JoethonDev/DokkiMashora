"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.applications.services import normalize_phone as application_normalize_phone
from apps.core.forms import StyledFormMixin
from apps.students.identity import (
    COUNTRY_CHOICES,
    identity_label,
    normalize_country_code,
    validate_identity_number,
)
from apps.students.models import StudentProfile

from .constants import (
    ACTIVE_ROLE_GROUPS,
    GROUP_STUDENT,
    USER_STATUS_ACTIVE,
    USER_STATUS_INACTIVE,
    USER_STATUS_SUSPENDED,
)
from .models import User


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

    StudentProfile is the authoritative source for a student's name and phone
    (dashboards and detail pages read it); the User copies are kept in sync
    because admin lists and the student header still read them. Email remains
    on User only and is not editable here.
    """

    full_name = forms.CharField(label=_("Full name"), max_length=150, widget=forms.TextInput())
    full_name_ar = forms.CharField(label=_("Full name (Arabic)"), max_length=150, required=False, widget=forms.TextInput())
    phone = forms.CharField(label=_("Phone number"), max_length=32, widget=forms.TextInput())

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            raise ValidationError(_("Phone number is required."))
        normalized = application_normalize_phone(phone)
        if len(normalized) < 7:
            raise ValidationError(_("Enter a valid mobile number."))
        profile = getattr(self.user, "student_profile", None)
        profile_pk = profile.pk if profile is not None else None
        for other in User.objects.exclude(pk=self.user.pk).exclude(phone=""):
            if application_normalize_phone(other.phone) == normalized:
                raise ValidationError(_("This phone number is already in use."))
        if profile is not None:
            for other in StudentProfile.objects.exclude(pk=profile_pk).exclude(phone=""):
                if application_normalize_phone(other.phone) == normalized:
                    raise ValidationError(_("This phone number is already in use."))
        elif StudentProfile.objects.exclude(phone="").filter(phone=phone).exists():
            raise ValidationError(_("This phone number is already in use."))
        return phone


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
            "national_id",
            "status",
            "is_active",
            "is_staff",
        )


class UserEditForm(StyledFormMixin, forms.ModelForm):
    email = forms.EmailField(label=_("Email"), widget=forms.EmailInput())
    full_name = forms.CharField(label=_("Full name"), max_length=150, required=False, widget=forms.TextInput())
    full_name_ar = forms.CharField(label=_("Full name (Arabic)"), max_length=150, required=False, widget=forms.TextInput())
    phone = forms.CharField(label=_("Phone number"), max_length=32, required=False, widget=forms.TextInput())
    country_code = forms.ChoiceField(
        label=_("Country"),
        choices=[],
        required=False,
        widget=forms.Select(),
    )
    national_id = forms.CharField(label=_("National ID"), max_length=32, required=False, widget=forms.TextInput())
    status = forms.ChoiceField(label=_("Status"), choices=User._meta.get_field("status").choices, widget=forms.Select())
    is_active = forms.BooleanField(label=_("Account active"), required=False)
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    gender = forms.ChoiceField(label=_("Gender"), choices=StudentProfile._meta.get_field("gender").choices, required=False, widget=forms.Select())
    address = forms.CharField(label=_("Address"), required=False, widget=forms.TextInput())
    education = forms.ChoiceField(label=_("Education"), choices=StudentProfile._meta.get_field("education").choices, required=False, widget=forms.Select())
    specialization = forms.CharField(label=_("Specialization"), required=False, widget=forms.TextInput())
    employer = forms.CharField(label=_("Employer"), required=False, widget=forms.TextInput())
    current_job = forms.CharField(label=_("Current job"), required=False, widget=forms.TextInput())
    church = forms.CharField(label=_("Church"), required=False, widget=forms.TextInput())
    priest_name = forms.CharField(label=_("Priest name"), required=False, widget=forms.TextInput())
    priest_contact = forms.CharField(label=_("Priest contact"), required=False, widget=forms.TextInput())
    new_password = forms.CharField(label=_("New password"), required=False, strip=False, widget=forms.PasswordInput())
    role = forms.ChoiceField(label=_("Role"), choices=[(role, _(role)) for role in ACTIVE_ROLE_GROUPS], required=False, widget=forms.Select())

    class Meta:
        model = User
        fields = ("email", "full_name", "full_name_ar", "phone", "country_code", "national_id", "status", "is_active")

    STUDENT_ONLY_FIELDS = [
        "country_code", "national_id", "date_of_birth", "gender", "address",
        "education", "specialization", "employer", "current_job", "church",
        "priest_name", "priest_contact",
    ]

    def __init__(self, *args, **kwargs):
        staff_only = kwargs.pop("staff_only", False)
        super().__init__(*args, **kwargs)
        self.fields["country_code"].choices = [("", _("Select country"))] + COUNTRY_CHOICES
        if staff_only:
            self.fields["role"].choices = [(role, _(role)) for role in ACTIVE_ROLE_GROUPS if role != GROUP_STUDENT]
            for fname in self.STUDENT_ONLY_FIELDS:
                if fname in self.fields:
                    del self.fields[fname]
        else:
            self.fields["role"].choices = [(GROUP_STUDENT, _(GROUP_STUDENT))]
        profile = getattr(self.instance, "student_profile", None)
        if not self.instance.pk:
            self.fields["new_password"].required = True
            self.fields["role"].required = True
        else:
            self.initial["role"] = self.instance.primary_role
            if not staff_only:
                self.fields["role"].disabled = True
        role = self.data.get("role") or self.initial.get("role")
        if role == GROUP_STUDENT:
            for name in ("full_name", "phone", "country_code", "national_id", "date_of_birth", "gender", "address", "education"):
                if name in self.fields:
                    self.fields[name].required = True
        if profile is not None:
            self.fields["full_name"].initial = profile.full_name
            self.fields["phone"].initial = profile.phone
            self.fields["national_id"].initial = profile.national_id
            self.fields["date_of_birth"].initial = profile.date_of_birth
            self.fields["gender"].initial = profile.gender
            self.fields["address"].initial = profile.address
            self.fields["education"].initial = profile.education
            self.fields["specialization"].initial = profile.specialization
            self.fields["employer"].initial = profile.employer
            self.fields["current_job"].initial = profile.current_job
            self.fields["church"].initial = profile.church
            self.fields["priest_name"].initial = profile.priest_name
            self.fields["priest_contact"].initial = profile.priest_contact

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            raise ValidationError(_("Phone number is required."))
        normalized = application_normalize_phone(phone)
        if len(normalized) < 7:
            raise ValidationError(_("Enter a valid mobile number."))
        profile = getattr(self.instance, "student_profile", None)
        profile_pk = profile.pk if profile is not None else None
        for other in User.objects.exclude(pk=self.instance.pk).exclude(phone=""):
            if application_normalize_phone(other.phone) == normalized:
                raise ValidationError(_("This phone number is already in use."))
        if profile is not None and profile_pk is not None:
            for other in StudentProfile.objects.exclude(pk=profile_pk).exclude(phone=""):
                if application_normalize_phone(other.phone) == normalized:
                    raise ValidationError(_("This phone number is already in use."))
        elif profile is None and StudentProfile.objects.exclude(phone="").filter(phone=phone).exists():
            raise ValidationError(_("This phone number is already in use."))
        return phone

    def clean_national_id(self):
        raw_id = (self.cleaned_data.get("national_id") or "").strip()
        profile = getattr(self.instance, "student_profile", None)
        cc_raw = self.cleaned_data.get("country_code", "")
        country_code = normalize_country_code(cc_raw or self.instance.country_code or "")

        if profile is not None:
            label = identity_label(country_code)
            if not raw_id:
                raise ValidationError(
                    _("%(label)s is required.") % {"label": label}
                )
            try:
                normalized_id = validate_identity_number(country_code, raw_id)
            except ValidationError as exc:
                raise ValidationError(str(exc)) from exc

            profile_pk = profile.pk if profile is not None else None
            qs = User.objects.exclude(pk=self.instance.pk).filter(national_id=normalized_id)
            if qs.exists():
                raise ValidationError(_("This identity number is already in use."))
            profile_qs = StudentProfile.objects.exclude(pk=profile_pk).filter(national_id=normalized_id)
            if profile_qs.exists():
                raise ValidationError(_("This identity number is already in use."))
            return normalized_id
        return raw_id

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
        user.email = data["email"]
        user.full_name = data.get("full_name", "")
        user.full_name_ar = data.get("full_name_ar", "")
        user.phone = data.get("phone", "")
        user.country_code = data.get("country_code", user.country_code)
        user.national_id = data.get("national_id", "")
        requested_status = data.get("status", user.status)
        requested_active = bool(data.get("is_active"))
        if requested_active:
            user.status = USER_STATUS_ACTIVE
            user.is_active = True
        elif requested_status == USER_STATUS_SUSPENDED:
            user.status = USER_STATUS_SUSPENDED
            user.is_active = False
        else:
            user.status = USER_STATUS_INACTIVE
            user.is_active = False
        if data.get("new_password"):
            user.set_password(data["new_password"])
        if commit:
            user.save()
            role = data.get("role")
            if role:
                user.groups.set([Group.objects.get(name=role)])
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
        return user


class UserUploadForm(StyledFormMixin, forms.Form):
    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("Upload a CSV file with columns: email, full_name"),
        widget=forms.FileInput(attrs={"accept": ".csv"}),
    )

    def clean_csv_file(self):
        file = self.cleaned_data.get("csv_file")
        if not file:
            raise ValidationError(_("No file provided"))
        if not file.name.endswith(".csv"):
            raise ValidationError(_("Only CSV files are accepted"))
        if file.size > 10 * 1024 * 1024:
            raise ValidationError(
                _("File size exceeds the maximum of 10 MB")
            )
        if file.size == 0:
            raise ValidationError(_("Uploaded file is empty"))
        return file
