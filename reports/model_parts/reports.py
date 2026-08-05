from __future__ import annotations

from .base import *
from .schools import School, Teacher, ReportType

class Report(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
        verbose_name="المدرسة",
        db_index=True,
    )

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
        db_index=True,
        verbose_name="المعلم (حساب)",
    )

    # اسم المعلم وقت الإنشاء (للتجميد)
    teacher_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="اسم المعلم (وقت الإنشاء)",
        help_text="يُحفظ هنا الاسم الظاهر بغض النظر عن تغيّر اسم الحساب لاحقًا.",
    )

    title = models.CharField("العنوان / البرنامج", max_length=255, db_index=True)
    report_date = models.DateField("تاريخ التقرير / البرنامج", db_index=True)
    academic_year = models.CharField(
        "السنة الدراسية (هجري)",
        max_length=9,
        blank=True,
        default="",
        help_text="تُملأ تلقائيًا من إعدادات المدرسة عند إنشاء التقرير.",
        db_index=True,
    )
    day_name = models.CharField("اليوم", max_length=20, blank=True, null=True)

    beneficiaries_count = models.PositiveIntegerField(
        "عدد المستفيدين",
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="اتركه فارغًا إذا لا ينطبق",
    )

    idea = models.TextField("الوصف / فكرة التقرير", blank=True, null=True)
    goal = models.TextField("الهدف", blank=True, default="")
    implementation_method = models.TextField("آلية التنفيذ", blank=True, default="")
    results = models.TextField("النتائج", blank=True, default="")
    recommendations = models.TextField("التوصيات", blank=True, default="")

    # يتحكم المستخدم في البنود التي تظهر داخل النسخة النهائية من التقرير.
    # القيم الافتراضية تحافظ على مظهر التقارير السابقة دون تغيير.
    show_goal = models.BooleanField("إظهار الهدف", default=False)
    show_details = models.BooleanField("إظهار تفاصيل التقرير", default=True)
    show_implementation = models.BooleanField("إظهار آلية التنفيذ", default=False)
    show_results = models.BooleanField("إظهار النتائج", default=False)
    show_recommendations = models.BooleanField("إظهار التوصيات", default=False)
    show_beneficiaries = models.BooleanField("إظهار عدد المستفيدين", default=True)

    # التصنيف ديناميكي عبر FK
    category = models.ForeignKey(
        "ReportType",
        on_delete=models.PROTECT,     # منع حذف النوع إن كان مستخدمًا
        null=True, blank=True,        # مؤقتًا لتسهيل الهجرة؛ يمكن جعلها إلزامية لاحقًا
        verbose_name="التصنيف",
        related_name="reports",
        db_index=True,
    )

    image1 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image2 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image3 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image4 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])

    # حجم ملفات هذا السجل (بايت) — يُحدّث تلقائيًا لتتبّع التخزين بلا قراءة شبكية عند الفحص
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)


    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["teacher", "category"]),
            models.Index(fields=["report_date"]),
            # ✅ في الإنتاج غالبًا نستعلم دائمًا داخل مدرسة محددة
            models.Index(fields=["school", "report_date"]),
            models.Index(fields=["school", "created_at"]),
            models.Index(fields=["school", "category"]),
            models.Index(fields=["school", "academic_year", "-report_date", "-id"]),
            # ✅ my_reports: filter(teacher, school) + order_by(-report_date, -id)
            models.Index(fields=["teacher", "school", "-report_date", "-id"]),
            # ✅ admin_reports: filter(school) + order_by(-report_date, -id)
            models.Index(fields=["school", "-report_date", "-id"]),
        ]
        verbose_name = "تقرير"
        verbose_name_plural = "التقارير"

    def __str__(self):
        display_name = self.teacher_name.strip() if self.teacher_name else getattr(self.teacher, "name", "")
        cat = getattr(self.category, "name", "بدون تصنيف")
        return f"{self.title} - {cat} - {display_name} ({self.report_date})"

    @property
    def teacher_display_name(self) -> str:
        return (self.teacher_name or getattr(self.teacher, "name", "") or "").strip()

    def save(self, *args, **kwargs):
        # اليوم باللغة العربية
        if self.report_date and not self.day_name:
            days = {
                1: "الاثنين", 2: "الثلاثاء", 3: "الأربعاء", 4: "الخميس",
                5: "الجمعة", 6: "السبت", 7: "الأحد"
            }
            try:
                self.day_name = days.get(self.report_date.isoweekday())
            except Exception:
                pass

        # تجميد اسم المعلّم وقت الإنشاء إن لم يُملأ
        if not self.teacher_name and getattr(self, "teacher_id", None):
            try:
                self.teacher_name = getattr(self.teacher, "name", "") or ""
            except Exception:
                pass

        if self.academic_year:
            try:
                self.academic_year = _normalize_academic_year_hijri(self.academic_year)
            except Exception:
                pass
        elif getattr(self, "school_id", None):
            try:
                current_year = (getattr(self.school, "current_academic_year", "") or "").strip()
                if not current_year:
                    years = getattr(self.school, "allowed_academic_years", None) or []
                    if years:
                        current_year = str(sorted(years)[-1])
                if current_year:
                    self.academic_year = _normalize_academic_year_hijri(current_year)
            except Exception:
                pass

        super().save(*args, **kwargs)


class PlatformSettings(models.Model):
    """إعدادات عامة للمنصة تُدار من مدير النظام.

    نستخدم سجلًا واحدًا فقط (singleton) ونتعامل معه عبر get_solo().
    """

    share_link_default_days = models.PositiveSmallIntegerField(
        "مدة صلاحية رابط المشاركة (بالأيام)",
        default=7,
        help_text="المدة الافتراضية لروابط مشاركة التقارير/ملفات الإنجاز.",
    )
    archive_addon_annual_price = models.DecimalField(
        "سعر إضافة الأرشفة السنوي",
        max_digits=10,
        decimal_places=2,
        default=399,
        validators=[MinValueValidator(0)],
        help_text="السعر الذي يظهر لمدير المدرسة عند طلب تفعيل أو تجديد الأرشفة.",
    )
    archive_included_storage_gb = models.PositiveIntegerField(
        "مساحة الأرشفة المضمنة (GB)",
        default=50,
        validators=[MinValueValidator(1)],
        help_text="المساحة التي يحصل عليها العميل عند تفعيل إضافة الأرشفة.",
    )
    archive_storage_block_gb = models.PositiveIntegerField(
        "حجم باقة زيادة التخزين (GB)",
        default=50,
        validators=[MinValueValidator(1)],
        help_text="حجم كل وحدة زيادة مساحة، مثال: 50GB.",
    )
    archive_storage_block_price = models.DecimalField(
        "سعر باقة زيادة التخزين",
        max_digits=10,
        decimal_places=2,
        default=149,
        validators=[MinValueValidator(0)],
        help_text="سعر كل وحدة زيادة مساحة تخزين للأرشيف.",
    )
    free_storage_mb = models.PositiveIntegerField(
        "حد التخزين الأدنى لمدرسة بلا اشتراك فعّال (ميجابايت)",
        default=1024,
        help_text=(
            "يُطبّق فقط على مدرسة بلا اشتراك فعّال. المدارس المشتركة تحصل على مساحة "
            "مشتقة من سعة المعلمين. القيمة بالميجابايت (1024MB = 1GB). "
            "ضع 0 لإلغاء الحد (تخزين غير محدود)."
        ),
    )
    storage_mb_per_teacher = models.PositiveIntegerField(
        "المساحة الأساسية لكل معلم (ميجابايت)",
        default=400,
        help_text=(
            "تُضرب في سعة المعلمين المشتراة لتحديد المساحة الأساسية للمدرسة، "
            "فتكبر المساحة بنفس نسبة نمو الفريق. "
            "مثال: 400MB × سعة 50 معلماً = 19.5GB. ضع 0 لإلغاء المساحة الأساسية المشتقة."
        ),
    )
    maintenance_mode_enabled = models.BooleanField(
        "تفعيل وضع الصيانة والتطوير",
        default=False,
        db_index=True,
        help_text="عند التفعيل تظهر شاشة الصيانة للمستخدمين ولا يمكنهم استخدام الموقع حتى إيقافها.",
    )
    maintenance_message = models.TextField(
        "رسالة الصيانة",
        blank=True,
        default="",
        help_text="رسالة اختيارية تظهر للمستخدمين في شاشة الصيانة.",
    )
    mansour_public_enabled = models.BooleanField(
        "إظهار المساعد منصور",
        default=True,
        db_index=True,
        help_text="إظهار منصور للزوار في الصفحة العامة والسماح باستخدامه.",
    )
    report_ai_enabled = models.BooleanField(
        "إظهار تحسين التقارير",
        default=True,
        db_index=True,
        help_text="إظهار أداة تحسين صياغة التقرير والسماح باستدعائها.",
    )
    internal_ai_help_enabled = models.BooleanField(
        "إظهار المساعدة داخل النظام",
        default=True,
        db_index=True,
        help_text="إظهار أداة المساعدة العائمة داخل الصفحات بعد تسجيل الدخول.",
    )

    updated_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settings_updates",
        verbose_name="آخر تعديل بواسطة",
    )
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)

    class Meta:
        verbose_name = "إعدادات المنصة"
        verbose_name_plural = "إعدادات المنصة"

    @classmethod
    def get_solo(cls) -> "PlatformSettings":
        obj = cls.objects.order_by("id").first()
        if obj is not None:
            return obj
        return cls.objects.create()

    def __str__(self) -> str:
        return "إعدادات المنصة"

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        try:
            from django.core.cache import cache

            cache.delete("platform_maintenance_state_v1")
            cache.delete("platform_storage_mb_per_teacher_v1")
        except Exception:
            pass
        try:
            from ..ai_features import clear_platform_ai_feature_cache

            clear_platform_ai_feature_cache()
        except Exception:
            pass
        return result


def get_share_link_default_days(school: Optional["School"] = None) -> int:
    """يرجع مدة صلاحية روابط المشاركة بالأيام.

    الأولوية:
    1. القيمة المحددة في نموذج School (إن تم تمريرها)
    2. settings.SHARE_LINK_DEFAULT_DAYS
    3. القيمة الافتراضية 7 أيام
    
    Args:
        school: نموذج المدرسة (اختياري)
    
    Returns:
        عدد الأيام (الحد الأدنى 1)
    """
    days = 7  # القيمة الافتراضية
    
    # محاولة قراءة القيمة من المدرسة
    if school is not None:
        try:
            school_days = getattr(school, "share_link_default_days", None)
            if school_days is not None:
                days = int(school_days)
        except Exception:
            pass
    
    # إذا لم يتم تمرير مدرسة أو لم تكن لديها قيمة، نقرأ من settings
    if days == 7:  # لم يتم تعديلها من المدرسة
        try:
            days = int(getattr(settings, "SHARE_LINK_DEFAULT_DAYS", 7))
        except Exception:
            days = 7
    
    # التأكد من أن القيمة موجبة
    if days <= 0:
        days = 7
    
    return days


# =========================
# روابط مشاركة عامة (بدون حساب)
# =========================
class ShareLink(models.Model):
    class Kind(models.TextChoices):
        REPORT = "report", "تقرير"
        ACHIEVEMENT = "achievement", "ملف إنجاز"

    token = models.CharField("Token", max_length=64, unique=True, db_index=True)
    kind = models.CharField("النوع", max_length=20, choices=Kind.choices, db_index=True)

    created_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="تم الإنشاء بواسطة",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="المدرسة",
        db_index=True,
    )

    report = models.ForeignKey(
        "Report",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="التقرير",
        db_index=True,
    )
    achievement_file = models.ForeignKey(
        "TeacherAchievementFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="ملف الإنجاز",
        db_index=True,
    )

    is_active = models.BooleanField("مفعّل", default=True, db_index=True)
    expires_at = models.DateTimeField("ينتهي في", db_index=True)
    last_accessed_at = models.DateTimeField("آخر وصول", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)

    class Meta:
        verbose_name = "رابط مشاركة"
        verbose_name_plural = "روابط مشاركة"
        indexes = [
            models.Index(fields=["kind", "is_active", "expires_at"]),
            models.Index(fields=["report", "is_active", "expires_at"]),
            models.Index(fields=["achievement_file", "is_active", "expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                name="sharelink_kind_target_consistent",
                check=(
                    models.Q(kind="report", report__isnull=False, achievement_file__isnull=True)
                    | models.Q(kind="achievement", report__isnull=True, achievement_file__isnull=False)
                ),
            )
        ]

    @staticmethod
    def default_expires_at() -> timezone.datetime:
        return timezone.now() + timedelta(days=get_share_link_default_days())

    @staticmethod
    def generate_token() -> str:
        # طول ~43 حرف عند 32 bytes (مع هامش)
        return secrets.token_urlsafe(32)

    @property
    def is_expired(self) -> bool:
        try:
            return timezone.now() >= self.expires_at
        except Exception:
            return True

    def __str__(self) -> str:
        target = self.report_id or self.achievement_file_id
        return f"{self.get_kind_display()} ({target})"


# =========================
# منظومة التذاكر الموحّدة
# =========================
MAX_ATTACHMENT_MB = 5
_MAX_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

