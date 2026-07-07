"""Base models for the LMS."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

LANDING_CONTENT_DEFAULTS = {
    "en": {
        "hero_eyebrow": "Coptic Orthodox family formation • Egypt",
        "hero_title": "Stronger families begin with better understanding.",
        "hero_body": "Christ-centered, practical learning for Egyptian Coptic Orthodox parents, couples, church servants, counselors, and everyone called to help a family grow.",
        "hero_primary": "Explore our programs",
        "hero_secondary": "Student login",
        "announcement_title": "Registration open",
        "announcement_body": "Applications are open through the official program application flow.",
        "intro_eyebrow": "Learning that changes daily life",
        "intro_title": "Orthodox faith, sound psychology, and guidance you can use.",
        "intro_body": "Rooted in the life of the Coptic Orthodox Church in Egypt, our courses turn psychology and family-care principles into calm conversations, healthier boundaries, and practical tools for home and service.",
        "benefits_eyebrow": "Formation for real family life",
        "benefits_title": "Learn deeply, practice safely, and serve with wisdom.",
        "benefits_body": "Every program connects Orthodox spiritual formation with responsible psychological knowledge and practical Egyptian family realities.",
        "benefit_1_title": "Faith-rooted",
        "benefit_1_body": "Coptic Orthodox identity shapes the purpose, language, and ethics of family care.",
        "benefit_2_title": "Practically useful",
        "benefit_2_body": "Cases, conversations, and tools move learning from the classroom into home and service.",
        "benefit_3_title": "Psychologically responsible",
        "benefit_3_body": "Sound concepts, clear boundaries, and referral awareness protect both families and servants.",
        "benefit_4_title": "Built for Egypt",
        "benefit_4_body": "Examples and challenges reflect the culture, pressures, and church life of Egyptian families.",
        "parenting_eyebrow": "For parents",
        "parenting_title": "Understand the feeling behind the behavior.",
        "parenting_body": "Build connection without losing boundaries or your family’s Orthodox foundation. Learn age-aware tools for emotions, discipline, adolescence, screens, prayer, and the conversations that shape a child’s inner world.",
        "diploma_eyebrow": "Professional diploma",
        "diploma_title": "Turn care into confident, responsible practice.",
        "diploma_body": "A structured journey for Coptic Orthodox counselors, church servants, and family-support professionals—combining applied psychology, pastoral awareness, supervised reflection, and Egyptian real-world cases.",
        "programs_eyebrow": "Choose your next step",
        "programs_title": "Choose the depth that fits your calling.",
        "programs_body": "Availability below is live and comes directly from the institute’s academic and application system.",
        "diplomas_eyebrow": "Structured formation",
        "diplomas_title": "Professional diplomas",
        "diplomas_body": "Longer learning journeys with guided study, multiple courses, and an entrance assessment when a new intake opens.",
        "courses_eyebrow": "Focused learning",
        "courses_title": "Standalone courses",
        "courses_body": "Shorter, focused programs using the latest active edition available for application.",
        "status_open": "Applications open now",
        "status_closed": "Not currently accepting applications",
        "success_groups": "learning groups launched",
        "diploma_courses_label": "courses in this diploma",
        "current_edition": "Latest active edition",
        "entrance_note": "The diploma application path includes an entrance assessment.",
        "apply_now": "Apply now",
        "quote": "Faithful family care does not offer perfect answers. It helps us listen with wisdom, understand with compassion, and respond in the spirit of Christ.",
        "quote_name": "The Logos learning promise",
        "quote_role": "Coptic Orthodox faith and compassionate knowledge, practiced together",
        "cta_eyebrow": "Your next chapter can start here",
        "cta_title": "Learn with depth. Care with confidence.",
        "cta_body": "Join an Egyptian Coptic Orthodox learning community built for healthier homes and more thoughtful church family support.",
        "cta_label": "Apply for a program",
        "footer_tagline": "Coptic Orthodox family formation and practical psychology for stronger Egyptian homes and wiser church care.",
        "footer_address": "Dokki, Giza, Egypt",
        "footer_about_title": "About the institute",
        "footer_links_title": "Explore",
        "footer_contact_title": "Contact",
        "footer_follow_title": "Follow",
        "footer_programs_link": "Diplomas and courses",
        "footer_apply_link": "Apply now",
        "footer_login_link": "Student login",
        "footer_hours": "Office hours: Saturday–Thursday, 10:00–18:00",
        "footer_email_placeholder": "info@your-domain.com",
        "footer_phone_placeholder": "+20 10 0000 0000",
        "footer_note": "Serving Coptic Orthodox families and church servants across Egypt.",
        "stat_1": "Active diplomas",
        "stat_2": "Learning groups launched",
        "stat_3": "Standalone courses",
        "stat_4": "Published lectures",
    },
    "ar": {
        "hero_eyebrow": "تكوين أسري قبطي أرثوذكسي • مصر",
        "hero_title": "الأسرة الأقوى تبدأ بفهم أعمق.",
        "hero_body": "تعلّم عملي محوره المسيح للوالدين والمقبلين على الزواج والخدام والمرشدين في الكنيسة القبطية الأرثوذكسية بمصر.",
        "hero_primary": "اكتشف برامجنا",
        "hero_secondary": "دخول الطلاب",
        "announcement_title": "التقديم مفتوح",
        "announcement_body": "التقديم متاح الآن من خلال مسار التقديم الرسمي للبرامج.",
        "intro_eyebrow": "تعلّم يغيّر الحياة اليومية",
        "intro_title": "إيمان أرثوذكسي وعلم نفس رصين وإرشاد قابل للتطبيق.",
        "intro_body": "من قلب حياة الكنيسة القبطية الأرثوذكسية في مصر، نحوّل مبادئ علم النفس والرعاية الأسرية إلى حوار هادئ وحدود صحية وأدوات عملية للبيت والخدمة.",
        "benefits_eyebrow": "تكوين للحياة الأسرية الواقعية",
        "benefits_title": "تعلّم بعمق وطبّق بأمان واخدم بحكمة.",
        "benefits_body": "يربط كل برنامج بين التكوين الروحي الأرثوذكسي والمعرفة النفسية المسؤولة وواقع الأسرة المصرية العملي.",
        "benefit_1_title": "متجذر في الإيمان",
        "benefit_1_body": "تشكل الهوية القبطية الأرثوذكسية هدف الرعاية الأسرية ولغتها وأخلاقياتها.",
        "benefit_2_title": "قابل للتطبيق",
        "benefit_2_body": "تنقل الحالات والحوارات والأدوات التعلّم من القاعة إلى البيت والخدمة.",
        "benefit_3_title": "مسؤول نفسيًا",
        "benefit_3_body": "تحمي المفاهيم الرصينة والحدود الواضحة والوعي بالإحالة الأسرة والخادم معًا.",
        "benefit_4_title": "مصمم لمصر",
        "benefit_4_body": "تعكس الأمثلة والتحديات ثقافة الأسرة المصرية وضغوطها وحياتها الكنسية.",
        "parenting_eyebrow": "للوالدين",
        "parenting_title": "افهم الشعور وراء السلوك.",
        "parenting_body": "ابنِ التواصل دون أن تفقد الحدود أو أساس الأسرة الأرثوذكسي. تعلّم أدوات تناسب العمر للتعامل مع المشاعر والتأديب والمراهقة والشاشات والصلاة والحوارات التي تشكّل عالم الطفل الداخلي.",
        "diploma_eyebrow": "دبلومة مهنية",
        "diploma_title": "حوّل الاهتمام إلى ممارسة واعية وواثقة.",
        "diploma_body": "رحلة تعليمية منظمة للمرشدين والخدام والمتخصصين في دعم الأسرة القبطية الأرثوذكسية، تجمع بين علم النفس التطبيقي والوعي الرعوي والتأمل الموجّه وحالات واقعية من المجتمع المصري.",
        "programs_eyebrow": "اختر خطوتك التالية",
        "programs_title": "اختر عمق التعلّم المناسب لدعوتك.",
        "programs_body": "حالة الإتاحة أدناه مباشرة وتأتي من النظام الأكاديمي ونظام التقديم بالمعهد.",
        "diplomas_eyebrow": "تكوين منظم",
        "diplomas_title": "الدبلومات المهنية",
        "diplomas_body": "رحلات تعليمية أعمق تشمل دراسة موجهة وعدة كورسات واختبار قبول عند فتح دفعة جديدة.",
        "courses_eyebrow": "تعلّم مركز",
        "courses_title": "الكورسات المستقلة",
        "courses_body": "برامج أقصر ومركزة تستخدم أحدث نسخة نشطة ومتاحة للتقديم.",
        "status_open": "التقديم مفتوح الآن",
        "status_closed": "لا يوجد تقديم متاح حاليًا",
        "success_groups": "مجموعات تعليمية انطلقت",
        "diploma_courses_label": "كورسات داخل الدبلومة",
        "current_edition": "أحدث نسخة نشطة",
        "entrance_note": "يشمل مسار التقديم للدبلومة اختبار قبول.",
        "apply_now": "قدّم الآن",
        "quote": "الرعاية الأسرية الأمينة لا تقدم إجابات مثالية، بل تساعدنا أن نسمع بحكمة ونفهم بمحبة ونستجيب بروح المسيح.",
        "quote_name": "وعد لوجوس التعليمي",
        "quote_role": "إيمان قبطي أرثوذكسي ومعرفة إنسانية نمارسهما معًا",
        "cta_eyebrow": "فصلك القادم يمكن أن يبدأ هنا",
        "cta_title": "تعلّم بعمق واهتم بثقة.",
        "cta_body": "انضم إلى مجتمع تعلّم قبطي أرثوذكسي مصري صُمم لبيوت أكثر صحة وخدمة أسرية كنسية أكثر وعيًا.",
        "cta_label": "قدّم في أحد البرامج",
        "footer_tagline": "تكوين أسري قبطي أرثوذكسي وعلم نفس عملي من أجل بيوت مصرية أقوى ورعاية كنسية أكثر حكمة.",
        "footer_address": "الدقي، الجيزة، مصر",
        "footer_about_title": "عن المعهد",
        "footer_links_title": "استكشف",
        "footer_contact_title": "تواصل معنا",
        "footer_follow_title": "تابعنا",
        "footer_programs_link": "الدبلومات والكورسات",
        "footer_apply_link": "قدّم الآن",
        "footer_login_link": "دخول الطلاب",
        "footer_hours": "مواعيد المكتب: السبت–الخميس، 10:00–18:00",
        "footer_email_placeholder": "info@your-domain.com",
        "footer_phone_placeholder": "+20 10 0000 0000",
        "footer_note": "نخدم الأسر القبطية الأرثوذكسية وخدام الكنيسة في أنحاء مصر.",
        "stat_1": "دبلومات نشطة",
        "stat_2": "مجموعات تعليمية انطلقت",
        "stat_3": "كورسات مستقلة",
        "stat_4": "محاضرات منشورة",
    },
}


def landing_content_defaults():
    return {language: values.copy() for language, values in LANDING_CONTENT_DEFAULTS.items()}


class TimeStampedModel(models.Model):
    """Abstract base model with timezone-aware created/updated timestamps."""

    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True
        get_latest_by = "created_at"
        ordering = ("-created_at",)


class LandingAnnouncement(TimeStampedModel):
    """Admin-controlled public landing announcement."""

    title = models.CharField(_("title"), max_length=160)
    message = models.CharField(_("message"), max_length=280)
    cta_label = models.CharField(_("button label"), max_length=80, blank=True)
    cta_url = models.CharField(_("button URL"), max_length=240, blank=True)
    starts_at = models.DateTimeField(_("starts at"), null=True, blank=True)
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("landing announcement")
        verbose_name_plural = _("landing announcements")
        ordering = ("-updated_at", "-created_at")

    def __str__(self):
        return self.title

    @classmethod
    def current(cls):
        now = timezone.now()
        return (
            cls.objects.filter(is_active=True)
            .filter(models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
            .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now))
            .first()
        )


class LandingPageSettings(TimeStampedModel):
    """Single editable landing-page preset."""

    content = models.JSONField(default=landing_content_defaults)
    hero_image = models.ImageField(upload_to="landing/", blank=True)
    parenting_image = models.ImageField(upload_to="landing/", blank=True)
    diploma_image = models.ImageField(upload_to="landing/", blank=True)
    show_live_stats = models.BooleanField(default=True)
    stat_value_1 = models.CharField(max_length=30, default="12+")
    stat_value_2 = models.CharField(max_length=30, default="8+")
    stat_value_3 = models.CharField(max_length=30, default="24+")
    stat_value_4 = models.CharField(max_length=30, default="100+")
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    youtube_url = models.URLField(blank=True)

    class Meta:
        verbose_name = _("landing page settings")
        verbose_name_plural = _("landing page settings")

    def __str__(self):
        return str(_("Landing page settings"))

    @classmethod
    def load(cls):
        return cls.objects.first() or cls.objects.create()

    def localized_content(self, language_code):
        language = "ar" if language_code == "ar" else "en"
        return {**LANDING_CONTENT_DEFAULTS[language], **self.content.get(language, {})}

    def reset_to_defaults(self):
        self.content = {language: values.copy() for language, values in LANDING_CONTENT_DEFAULTS.items()}
        self.hero_image = self.parenting_image = self.diploma_image = None
        self.show_live_stats = True
        self.stat_value_1, self.stat_value_2, self.stat_value_3, self.stat_value_4 = (
            "12+",
            "8+",
            "24+",
            "100+",
        )
        self.contact_email = self.contact_phone = ""
        self.facebook_url = self.instagram_url = self.youtube_url = ""
        self.save()
