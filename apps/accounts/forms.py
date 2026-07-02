"""Authentication and profile forms."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserChangeForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.students.models import StudentProfile

from apps.applications.services import normalize_phone as application_normalize_phone

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
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


class ProfileForm(forms.ModelForm):
    """Allow the user to update their own basic profile fields."""

    class Meta:
        model = User
        fields = ("full_name", "full_name_ar", "phone")
        labels = {
            "full_name": _("Full name"),
            "full_name_ar": _("Full name (Arabic)"),
            "phone": _("Phone number"),
        }


class AdminUserCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": _("Email")}


class AdminUserChangeForm(UserChangeForm):
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


class UserEditForm(forms.ModelForm):
    email = forms.EmailField(label=_("Email"))
    full_name = forms.CharField(label=_("Full name"), max_length=150, required=False)
    full_name_ar = forms.CharField(label=_("Full name (Arabic)"), max_length=150, required=False)
    phone = forms.CharField(label=_("Phone number"), max_length=32, required=False)
    national_id = forms.CharField(label=_("National ID"), max_length=32, required=False)
    status = forms.ChoiceField(label=_("Status"), choices=User._meta.get_field("status").choices)
    is_active = forms.BooleanField(label=_("Account active"), required=False)
    date_of_birth = forms.DateField(label=_("Date of birth"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    gender = forms.ChoiceField(label=_("Gender"), choices=StudentProfile._meta.get_field("gender").choices, required=False)
    address = forms.CharField(label=_("Address"), required=False)
    education = forms.ChoiceField(label=_("Education"), choices=StudentProfile._meta.get_field("education").choices, required=False)
    specialization = forms.CharField(label=_("Specialization"), required=False)
    employer = forms.CharField(label=_("Employer"), required=False)
    current_job = forms.CharField(label=_("Current job"), required=False)
    church = forms.CharField(label=_("Church"), required=False)
    priest_name = forms.CharField(label=_("Priest name"), required=False)
    priest_contact = forms.CharField(label=_("Priest contact"), required=False)
    new_password = forms.CharField(label=_("New password"), required=False, strip=False, widget=forms.PasswordInput())

    class Meta:
        model = User
        fields = ("email", "full_name", "full_name_ar", "phone", "national_id", "status", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        profile = getattr(self.instance, "student_profile", None)
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
        national_id = (self.cleaned_data.get("national_id") or "").strip()
        if not national_id:
            raise ValidationError(_("National ID is required."))
        profile = getattr(self.instance, "student_profile", None)
        profile_pk = profile.pk if profile is not None else None
        qs = User.objects.exclude(pk=self.instance.pk).filter(national_id=national_id)
        if qs.exists():
            raise ValidationError(_("This national ID is already in use."))
        profile_qs = StudentProfile.objects.exclude(pk=profile_pk).filter(national_id=national_id)
        if profile_qs.exists():
            raise ValidationError(_("This national ID is already in use."))
        return national_id

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

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        profile = getattr(user, "student_profile", None)
        data = self.cleaned_data
        user.email = data["email"]
        user.full_name = data.get("full_name", "")
        user.full_name_ar = data.get("full_name_ar", "")
        user.phone = data.get("phone", "")
        user.national_id = data.get("national_id", "")
        user.status = data.get("status", user.status)
        user.is_active = bool(data.get("is_active"))
        if data.get("new_password"):
            user.set_password(data["new_password"])
        if commit:
            user.save()
            if profile is not None:
                profile.full_name = data.get("full_name", profile.full_name)
                profile.date_of_birth = data.get("date_of_birth") or profile.date_of_birth
                profile.gender = data.get("gender", profile.gender)
                profile.address = data.get("address", profile.address)
                profile.phone = data.get("phone", profile.phone)
                profile.national_id = data.get("national_id", profile.national_id)
                profile.education = data.get("education", profile.education)
                profile.specialization = data.get("specialization", profile.specialization)
                profile.employer = data.get("employer", profile.employer)
                profile.current_job = data.get("current_job", profile.current_job)
                profile.church = data.get("church", profile.church)
                profile.priest_name = data.get("priest_name", profile.priest_name)
                profile.priest_contact = data.get("priest_contact", profile.priest_contact)
                profile.save()
        return user


class UserUploadForm(forms.Form):
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
