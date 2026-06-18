"""Constants for the students and applications domains."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

GENDER_MALE = "male"
GENDER_FEMALE = "female"

GENDER_CHOICES = (
    (GENDER_MALE, _("Male")),
    (GENDER_FEMALE, _("Female")),
)

EDUCATION_SECONDARY = "secondary"
EDUCATION_BACHELOR = "bachelor"
EDUCATION_MASTER = "master"
EDUCATION_DOCTORATE = "doctorate"
EDUCATION_OTHER = "other"

EDUCATION_CHOICES = (
    (EDUCATION_SECONDARY, _("Secondary")),
    (EDUCATION_BACHELOR, _("Bachelor")),
    (EDUCATION_MASTER, _("Master")),
    (EDUCATION_DOCTORATE, _("Doctorate")),
    (EDUCATION_OTHER, _("Other")),
)
