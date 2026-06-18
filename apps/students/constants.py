"""Constants for the students and applications domains."""

from __future__ import annotations

GENDER_MALE = "male"
GENDER_FEMALE = "female"

GENDER_CHOICES = (
    (GENDER_MALE, "Male"),
    (GENDER_FEMALE, "Female"),
)

EDUCATION_SECONDARY = "secondary"
EDUCATION_BACHELOR = "bachelor"
EDUCATION_MASTER = "master"
EDUCATION_DOCTORATE = "doctorate"
EDUCATION_OTHER = "other"

EDUCATION_CHOICES = (
    (EDUCATION_SECONDARY, "Secondary"),
    (EDUCATION_BACHELOR, "Bachelor"),
    (EDUCATION_MASTER, "Master"),
    (EDUCATION_DOCTORATE, "Doctorate"),
    (EDUCATION_OTHER, "Other"),
)
