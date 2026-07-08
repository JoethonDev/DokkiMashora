"""Seed the default transactional email templates."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.communications.constants import (
    DESIGN_BRANDED,
    TEMPLATE_ACCEPTANCE,
    TEMPLATE_ACTIVATION,
    TEMPLATE_APPLICATION_PAYMENT_PENDING,
    TEMPLATE_APPLICATION_SURVEY_REQUEST,
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS,
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST,
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE,
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST,
    TEMPLATE_ENTRANCE_EXAM_FAIL,
    TEMPLATE_ENTRANCE_EXAM_INVITATION,
    TEMPLATE_ENTRANCE_EXAM_PASS,
    TEMPLATE_EXAM_INVITATION,
    TEMPLATE_PAYMENT_NOTIFICATION,
    TEMPLATE_RECEIVED,
    TEMPLATE_REJECTION,
)
from apps.communications.models import EmailTemplate

DEFAULT_TEMPLATES = {
    TEMPLATE_RECEIVED: {
        "name": "Registration received",
        "subject": "We received your registration",
        "body": (
            "Hello {{ full_name }},\n\n"
            "We received your registration for DokkiMashora. "
            "Our team will review it and get back to you shortly.\n\n"
            "National ID: {{ national_id }}\n"
            "Email: {{ email }}\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_INVITATION: {
        "name": "Entrance exam invitation",
        "subject": "Your entrance exam is ready",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your entrance exam is now available. "
            "You can take it from {{ exam_open_at }} until {{ exam_close_at }}.\n\n"
            "Please log in to start: {{ exam_url }}\n\n"
            "Good luck!\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_PASS: {
        "name": "Entrance exam passed",
        "subject": "You passed the entrance exam",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Congratulations! You have passed the entrance exam '{{ exam_title }}'.\n\n"
            "Your application has been accepted. We will contact you with the next steps.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_FAIL: {
        "name": "Entrance exam failed",
        "subject": "Entrance exam result",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Thank you for taking the entrance exam '{{ exam_title }}'.\n\n"
            "Unfortunately, you did not pass at this time.\n\n"
            "You are welcome to apply again in a future cycle.\n\n"
            "DokkiMashora admissions"
        ),
    },
    TEMPLATE_EXAM_INVITATION: {
        "name": "Exam invitation",
        "subject": "Your exam is ready",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your exam '{{ exam_title }}' is now available.\n\n"
            "Please log in to start: {{ exam_url }}\n\n"
            "Good luck!\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_ACCEPTANCE: {
        "name": "Acceptance",
        "subject": "Welcome to DokkiMashora",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Congratulations! Your application has been accepted. "
            "We will activate your account shortly and send you the activation link.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_REJECTION: {
        "name": "Rejection",
        "subject": "Update on your application",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Thank you for your interest in DokkiMashora. "
            "After review we are unable to accept your application at this time.\n\n"
            "Reason: {{ rejection_reason }}\n\n"
            "You are welcome to apply again in a future cycle.\n\n"
            "DokkiMashora admissions"
        ),
    },
    TEMPLATE_ACTIVATION: {
        "name": "Account activation",
        "subject": "Activate your account",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your account has been created. Your login email is {{ email }} and your initial password is {{ password }}.\n\n"
            "Please activate your account using the link below:\n\n"
            "{{ activation_url }}\n\n"
            "You can change your password after logging in.\n\n"
            "Welcome aboard!\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_PAYMENT_NOTIFICATION: {
        "name": "Payment notification",
        "subject": "Payment received for your enrollment",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Payment has been received for your enrollment.\n\n"
            "Amount: {{ amount }} {{ currency }}\n"
            "Date: {{ due_at }}\n"
            "Reference: {{ reference }}\n"
            "Status: {{ status }}\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS: {
        "name": "Course payment instructions",
        "subject": "Payment instructions for your course",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Thank you for applying to '{{ course_title }}'.\n\n"
            "To complete your enrollment, please follow the payment instructions below.\n\n"
            "Contact us at {{ staff_phone }} if you need assistance.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE: {
        "name": "Diploma entrance exam schedule",
        "subject": "Your entrance exam schedule for {{ diploma_title }}",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your application for '{{ diploma_title }}' ({{ cohort_name }}) has been received.\n\n"
            "The entrance exam '{{ exam_title }}' is scheduled.\n"
            "Exam period: {{ exam_open_at }} to {{ exam_close_at }}.\n\n"
            "Please log in to your dashboard to take the exam.\n\n"
            "Good luck!\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST: {
        "name": "Diploma interview request",
        "subject": "Interview request for your diploma application",
        "body": (
            "Hello {{ full_name }},\n\n"
            "We would like to invite you to an interview for your application to '{{ diploma_title }}' ({{ cohort_name }}).\n\n"
            "{% if interview_at %}Interview date: {{ interview_at }}\n{% endif %}"
            "{% if phone_number %}Contact phone: {{ phone_number }}\n{% endif %}"
            "This invitation expires on {{ expires_at }}.\n\n"
            "Please respond before the expiry date.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_APPLICATION_PAYMENT_PENDING: {
        "name": "Application payment pending",
        "subject": "Payment pending for your application",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your application for '{{ program_type }}' is pending payment.\n"
            "{% if course_title %}Course: {{ course_title }}\n{% endif %}"
            "{% if diploma_title %}Diploma: {{ diploma_title }}\n{% endif %}"
            "{% if cohort_name %}Cohort: {{ cohort_name }}\n{% endif %}"
            "Please contact us at {{ staff_phone }} for payment details.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST: {
        "name": "Diploma document upload request",
        "subject": "Document upload required for your diploma application",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your application for '{{ diploma_title }}' ({{ cohort_name }}) has been accepted. "
            "To proceed with enrollment, please upload the required documents.\n\n"
            "Upload link: {{ upload_url }}\n"
            "This link expires on {{ expires_at }}.\n\n"
            "Please upload:\n"
            "- A personal photo (persona image)\n"
            "- Your documents (one PDF or multiple images)\n\n"
            "Contact us at {{ staff_phone }} if you need assistance.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
    TEMPLATE_APPLICATION_SURVEY_REQUEST: {
        "name": "Application survey request",
        "subject": "Intake form required for your application",
        "body": (
            "Hello {{ full_name }},\n\n"
            "Your application for '{{ program_title }}' requires you to complete an intake form.\n\n"
            "Form: {{ form_title }}\n"
            "Link: {{ form_url }}\n"
            "This link expires on {{ expires_at }}.\n\n"
            "Please complete the form before the expiry date.\n\n"
            "Contact us at {{ staff_phone }} if you need assistance.\n\n"
            "Thank you,\nDokkiMashora admissions"
        ),
    },
}


class Command(BaseCommand):
    help = "Create or update the default email templates."

    def handle(self, *args, **options) -> None:
        for slug, payload in DEFAULT_TEMPLATES.items():
            template, created = EmailTemplate.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": payload["name"],
                    "subject": payload["subject"],
                    "body": payload["body"],
                    "is_active": True,
                    "is_enabled_for_action": True,
                    "design": DESIGN_BRANDED,
                },
            )
            self.stdout.write(
                self.style.SUCCESS(f"Template ensured: {slug}") if created else f"Template updated: {slug}"
            )
