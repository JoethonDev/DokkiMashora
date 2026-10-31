"""Seed the default transactional email templates.

Every default body is bilingual. Arabic is the default branch, English is the
conditional branch, and the subject is rendered through the same template
engine so it can follow the active language too.
"""

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


def _bi(english: str, arabic: str) -> str:
    """Return one bilingual email string with Arabic as the default branch."""
    return f"{{% if language_code == 'en' %}}{english}{{% else %}}{arabic}{{% endif %}}"


DEFAULT_TEMPLATES = {
    TEMPLATE_RECEIVED: {
        "name": "Registration received",
        "subject": _bi("We received your registration", "استلمنا تسجيلك"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "We received your registration for DokkiMashora. "
                "Our team will review it and get back to you shortly.\n\n"
                "National ID: {{ national_id }}\n"
                "Email: {{ email }}\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "استلمنا تسجيلك في دُكّي مشورة. "
                "سيراجعه فريقنا ويتواصل معك قريبًا.\n\n"
                "الرقم القومي: {{ national_id }}\n"
                "البريد الإلكتروني: {{ email }}\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_INVITATION: {
        "name": "Entrance exam invitation",
        "subject": _bi("Your entrance exam is ready", "اختبار القبول الخاص بك متاح"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your entrance exam is now available. "
                "You can take it from {{ exam_open_at }} until {{ exam_close_at }}.\n\n"
                "Please log in to start: {{ exam_url }}\n\n"
                "Good luck!\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "اختبار القبول الخاص بك متاح الآن. "
                "يمكنك أداؤه من {{ exam_open_at }} حتى {{ exam_close_at }}.\n\n"
                "يرجى تسجيل الدخول للبدء: {{ exam_url }}\n\n"
                "بالتوفيق!\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_PASS: {
        "name": "Entrance exam review notice",
        "subject": "{{ subject }}",
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your entrance exam '{{ exam_title }}' has been submitted successfully.\n\n"
                "The admissions office will review your submission and contact you with the next steps.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم تقديم اختبار القبول '{{ exam_title }}' بنجاح.\n\n"
                "ستقوم إدارة القبول بمراجعة تسليمك والتواصل معك بشأن الخطوات التالية.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_ENTRANCE_EXAM_FAIL: {
        "name": "Entrance exam review notice",
        "subject": "{{ subject }}",
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your entrance exam '{{ exam_title }}' has been submitted successfully.\n\n"
                "The admissions office will review your submission and contact you with the next steps.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم تقديم اختبار القبول '{{ exam_title }}' بنجاح.\n\n"
                "ستقوم إدارة القبول بمراجعة تسليمك والتواصل معك بشأن الخطوات التالية.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_EXAM_INVITATION: {
        "name": "Exam invitation",
        "subject": _bi("Your exam is ready", "اختبارك متاح"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your exam '{{ exam_title }}' is now available.\n\n"
                "Please log in to start: {{ exam_url }}\n\n"
                "Good luck!\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "اختبارك '{{ exam_title }}' متاح الآن.\n\n"
                "يرجى تسجيل الدخول للبدء: {{ exam_url }}\n\n"
                "بالتوفيق!\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_ACCEPTANCE: {
        "name": "Acceptance",
        "subject": _bi("Welcome to DokkiMashora", "أهلًا بك في دُكّي مشورة"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Congratulations! Your application has been accepted. "
                "We will activate your account shortly and send you the activation link.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تهانينا! تم قبول طلبك. "
                "سنفعّل حسابك قريبًا ونرسل لك رابط التفعيل.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_REJECTION: {
        "name": "Rejection",
        "subject": _bi("Update on your application", "تحديث بشأن طلبك"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Thank you for your interest in DokkiMashora. "
                "After review we are unable to accept your application at this time.\n\n"
                "Reason: {{ rejection_reason }}\n\n"
                "You are welcome to apply again in a future cycle.\n\n"
                "DokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "شكرًا لاهتمامك بدُكّي مشورة. "
                "بعد المراجعة، تعذّر قبول طلبك في هذه المرحلة.\n\n"
                "السبب: {{ rejection_reason }}\n\n"
                "نرحب بتقديمك مرة أخرى في دورة مستقبلية.\n\n"
                "إدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_ACTIVATION: {
        "name": "Account activation",
        "subject": _bi("Activate your account", "فعّل حسابك"),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your account has been created. Your login email is {{ email }}.\n\n"
                "Please activate your account using the link below and choose your password:\n\n"
                "{{ activation_url }}\n\n"
                "Welcome aboard!\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم إنشاء حسابك. بريد تسجيل الدخول هو {{ email }}.\n\n"
                "يرجى تفعيل حسابك عبر الرابط التالي واختيار كلمة المرور:\n\n"
                "{{ activation_url }}\n\n"
                "أهلًا بك!\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_PAYMENT_NOTIFICATION: {
        "name": "Payment notification",
        "subject": _bi(
            "Payment received for your enrollment",
            "تم استلام دفع تسجيلك",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Payment has been received for your enrollment.\n\n"
                "Amount: {{ amount }} {{ currency }}\n"
                "Date: {{ due_at }}\n"
                "Reference: {{ reference }}\n"
                "Status: {{ status }}\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم استلام الدفع الخاص بتسجيلك.\n\n"
                "المبلغ: {{ amount }} {{ currency }}\n"
                "التاريخ: {{ due_at }}\n"
                "المرجع: {{ reference }}\n"
                "الحالة: {{ status }}\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_COURSE_PAYMENT_INSTRUCTIONS: {
        "name": "Course payment instructions",
        "subject": _bi(
            "Payment instructions for your course",
            "تعليمات دفع الدورة",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Thank you for applying to '{{ course_title }}'.\n\n"
                "To complete your enrollment, please follow the payment instructions below.\n\n"
                "Contact us at {{ staff_phone }} if you need assistance.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "شكرًا لتقديمك على '{{ course_title }}'.\n\n"
                "لإكمال تسجيلك، يرجى اتباع تعليمات الدفع أدناه.\n\n"
                "تواصل معنا على {{ staff_phone }} إذا احتجت مساعدة.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_DIPLOMA_ENTRANCE_EXAM_SCHEDULE: {
        "name": "Diploma entrance exam schedule",
        "subject": _bi(
            "Your entrance exam schedule for {{ diploma_title }}",
            "جدول اختبار القبول لـ {{ diploma_title }}",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your application for '{{ diploma_title }}' ({{ cohort_name }}) has been received.\n\n"
                "The entrance exam '{{ exam_title }}' is scheduled.\n"
                "Exam period: {{ exam_open_at }} to {{ exam_close_at }}.\n\n"
                "Please log in to your dashboard to take the exam.\n\n"
                "Good luck!\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم استلام طلبك لـ '{{ diploma_title }}' ({{ cohort_name }}).\n\n"
                "تم جدولة اختبار القبول '{{ exam_title }}'.\n"
                "فترة الاختبار: من {{ exam_open_at }} إلى {{ exam_close_at }}.\n\n"
                "يرجى تسجيل الدخول إلى لوحة التحكم لأداء الاختبار.\n\n"
                "بالتوفيق!\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_DIPLOMA_INTERVIEW_REQUEST: {
        "name": "Diploma interview request",
        "subject": _bi(
            "Interview request for your diploma application",
            "طلب مقابلة بخصوص طلب الدبلومة",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "We would like to invite you to an interview for your application to '{{ diploma_title }}' ({{ cohort_name }}).\n\n"
                "{% if interview_at %}Interview date: {{ interview_at }}\n{% endif %}"
                "{% if phone_number %}Contact phone: {{ phone_number }}\n{% endif %}"
                "This invitation expires on {{ expires_at }}.\n\n"
                "Please respond before the expiry date.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "نود دعوتك لمقابلة بخصوص طلبك لـ '{{ diploma_title }}' ({{ cohort_name }}).\n\n"
                "{% if interview_at %}موعد المقابلة: {{ interview_at }}\n{% endif %}"
                "{% if phone_number %}هاتف التواصل: {{ phone_number }}\n{% endif %}"
                "تنتهي صلاحية هذه الدعوة في {{ expires_at }}.\n\n"
                "يرجى الرد قبل تاريخ الانتهاء.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_APPLICATION_PAYMENT_PENDING: {
        "name": "Application payment pending",
        "subject": _bi(
            "Payment pending for your application",
            "دفع طلبك قيد الانتظار",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your application for '{{ program_type }}' is pending payment.\n"
                "{% if course_title %}Course: {{ course_title }}\n{% endif %}"
                "{% if diploma_title %}Diploma: {{ diploma_title }}\n{% endif %}"
                "{% if cohort_name %}Cohort: {{ cohort_name }}\n{% endif %}"
                "Please contact us at {{ staff_phone }} for payment details.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "طلبك لـ '{{ program_type }}' في انتظار الدفع.\n"
                "{% if course_title %}الدورة: {{ course_title }}\n{% endif %}"
                "{% if diploma_title %}الدبلومة: {{ diploma_title }}\n{% endif %}"
                "{% if cohort_name %}الدفعة: {{ cohort_name }}\n{% endif %}"
                "يرجى التواصل معنا على {{ staff_phone }} لتفاصيل الدفع.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_DIPLOMA_DOCUMENT_UPLOAD_REQUEST: {
        "name": "Diploma document upload request",
        "subject": _bi(
            "Document upload required for your diploma application",
            "مطلوب رفع مستندات لطلب الدبلومة",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your application for '{{ diploma_title }}' ({{ cohort_name }}) has been accepted. "
                "To proceed with enrollment, please upload the required documents.\n\n"
                "Upload link: {{ upload_url }}\n"
                "This link expires on {{ expires_at }}.\n\n"
                "Please upload:\n"
                "- A personal photo\n"
                "- Your documents (one PDF or multiple images)\n\n"
                "Contact us at {{ staff_phone }} if you need assistance.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "تم قبول طلبك لـ '{{ diploma_title }}' ({{ cohort_name }}). "
                "للاستمرار في التسجيل، يرجى رفع المستندات المطلوبة.\n\n"
                "رابط الرفع: {{ upload_url }}\n"
                "تنتهي صلاحية هذا الرابط في {{ expires_at }}.\n\n"
                "يرجى رفع:\n"
                "- صورة شخصية\n"
                "- مستنداتك (ملف PDF واحد أو عدة صور)\n\n"
                "تواصل معنا على {{ staff_phone }} إذا احتجت مساعدة.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
    TEMPLATE_APPLICATION_SURVEY_REQUEST: {
        "name": "Application survey request",
        "subject": _bi(
            "Intake form required for your application",
            "مطلوب نموذج بيانات لطلبك",
        ),
        "body": _bi(
            (
                "Hello {{ full_name }},\n\n"
                "Your application for '{{ program_title }}' requires you to complete an intake form.\n\n"
                "Form: {{ form_title }}\n"
                "Link: {{ form_url }}\n"
                "This link expires on {{ expires_at }}.\n\n"
                "Please complete the form before the expiry date.\n\n"
                "Contact us at {{ staff_phone }} if you need assistance.\n\n"
                "Thank you,\nDokkiMashora admissions"
            ),
            (
                "مرحبًا {{ full_name }}،\n\n"
                "يتطلب طلبك لـ '{{ program_title }}' إكمال نموذج بيانات.\n\n"
                "النموذج: {{ form_title }}\n"
                "الرابط: {{ form_url }}\n"
                "تنتهي صلاحية هذا الرابط في {{ expires_at }}.\n\n"
                "يرجى إكمال النموذج قبل تاريخ الانتهاء.\n\n"
                "تواصل معنا على {{ staff_phone }} إذا احتجت مساعدة.\n\n"
                "شكرًا،\nإدارة القبول في دُكّي مشورة"
            ),
        ),
    },
}


class Command(BaseCommand):
    help = "Create or update the default email templates."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help=(
                "Only create templates that do not exist yet; never overwrite "
                "existing (admin-edited) templates. Used at application startup."
            ),
        )
        parser.add_argument(
            "--localize-existing",
            action="store_true",
            help=(
                "Update existing templates that are still single-language (no "
                "language_code branch). Admin-edited bilingual templates are left alone."
            ),
        )

    def handle(self, *args, **options) -> None:
        missing_only = options["missing_only"]
        localize_existing = options["localize_existing"]
        for slug, payload in DEFAULT_TEMPLATES.items():
            defaults = {
                "name": payload["name"],
                "subject": payload["subject"],
                "body": payload["body"],
                "is_active": True,
                "is_enabled_for_action": True,
                "design": DESIGN_BRANDED,
            }
            if missing_only:
                _template, created = EmailTemplate.objects.get_or_create(slug=slug, defaults=defaults)
            elif localize_existing:
                template = EmailTemplate.objects.filter(slug=slug).first()
                if template is None:
                    template = EmailTemplate.objects.create(slug=slug, **defaults)
                    created = True
                elif "language_code" in template.body:
                    self.stdout.write(f"Template already bilingual: {slug}")
                    continue
                else:
                    template.name = payload["name"]
                    template.subject = payload["subject"]
                    template.body = payload["body"]
                    template.is_enabled_for_action = True
                    template.save(
                        update_fields=["name", "subject", "body", "is_enabled_for_action", "updated_at"]
                    )
                    created = False
            else:
                _template, created = EmailTemplate.objects.update_or_create(slug=slug, defaults=defaults)
            if created:
                self.stdout.write(self.style.SUCCESS(f"Template created: {slug}"))
            elif missing_only:
                self.stdout.write(f"Template already present: {slug}")
            else:
                self.stdout.write(f"Template updated: {slug}")
