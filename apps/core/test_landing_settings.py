from datetime import timedelta

import pytest
from django.utils import timezone

from apps.academics.models import Cohort, Course, CourseVersion, Diploma
from apps.accounts.models import User
from apps.core.models import LandingPageSettings
from apps.entrance_exams.models import EntranceExam


@pytest.mark.django_db
def test_landing_settings_reset_restores_bilingual_preset():
    settings = LandingPageSettings.load()
    settings.content["en"]["hero_title"] = "Changed"
    settings.show_live_stats = False
    settings.contact_email = "hello@example.com"
    settings.save()

    settings.reset_to_defaults()

    assert settings.localized_content("en")["hero_title"] != "Changed"
    assert "Coptic Orthodox" in settings.localized_content("en")["hero_eyebrow"]
    assert "قبطي أرثوذكسي" in settings.localized_content("ar")["hero_eyebrow"]
    assert settings.localized_content("ar")["hero_title"]
    assert settings.show_live_stats is True
    assert settings.contact_email == ""


@pytest.mark.django_db
def test_landing_separates_open_programs_and_links_to_apply(client):
    creator = User.objects.create_user(email="creator@example.com", password="test")
    diploma = Diploma.objects.create(title="Family Counseling Diploma", created_by=creator)
    cohort = Cohort.objects.create(
        diploma=diploma,
        name="2026 intake",
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timedelta(days=90),
        created_by=creator,
    )
    now = timezone.now()
    EntranceExam.objects.create(
        title="Entrance assessment",
        diploma=diploma,
        cohort=cohort,
        duration_minutes=30,
        is_active=True,
        open_at=now - timedelta(days=1),
        close_at=now + timedelta(days=1),
        created_by=creator,
    )
    course = Course.objects.create(title="Positive Parenting Course", created_by=creator)
    CourseVersion.objects.create(course=course, label="2026", created_by=creator)
    CourseVersion.objects.create(course=course, label="2027 draft", is_active=False, created_by=creator)

    response = client.get("/en/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Professional diplomas" in content
    assert "Courses" in content
    assert f"target_id={cohort.pk}" in content
    assert f"target_id=course_{course.pk}" in content
    assert "Latest active edition: 2026" in content
    assert "2027 draft" not in content
    assert "Open cohorts" not in content

    registration = client.get(f"/en/applications/register/?program_type=diploma&target_id={cohort.pk}")
    assert registration.context["form"].initial == {
        "apply_program": True,
        "program_type": "diploma",
        "diploma": str(cohort.pk),
    }
    course_registration = client.get(
        f"/en/applications/register/?program_type=course&target_id=course_{course.pk}"
    )
    assert course_registration.context["form"].initial["program_target"] == f"course_{course.pk}"

    client.force_login(creator)
    signed_content = client.get("/en/").content.decode()
    assert "profile/apply/?program_type=diploma" in signed_content
