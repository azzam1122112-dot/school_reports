from __future__ import annotations

from .base import *

class School(models.Model):
    name = models.CharField("اسم المدرسة", max_length=200)
    class Stage(models.TextChoices):
        KG = "kg", "رياض أطفال"
        PRIMARY = "primary", "ابتدائي"
        MIDDLE = "middle", "متوسط"
        HIGH = "high", "ثانوي"

    class Gender(models.TextChoices):
        BOYS = "boys", "بنين"
        GIRLS = "girls", "بنات"

    code = models.SlugField(
        "المعرّف (code)",
        max_length=64,
        unique=True,
        help_text="كود قصير لتمييز المدرسة، يُستخدم في الاختيار والتقارير.",
    )
    stage = models.CharField(
        "المرحلة",
        max_length=16,
        choices=Stage.choices,
        default=Stage.PRIMARY,
    )
    gender = models.CharField(
        "بنين / بنات",
        max_length=8,
        choices=Gender.choices,
        default=Gender.BOYS,
    )
    phone = models.CharField("رقم الجوال", max_length=20, blank=True, null=True)
    city = models.CharField("المدينة", max_length=120, blank=True, null=True)
    is_active = models.BooleanField("نشطة؟", default=True)
    print_primary_color = models.CharField(
        "لون قالب الطباعة",
        max_length=9,
        blank=True,
        null=True,
        help_text="لون رئيسي لقالب الطباعة (مثلاً #2563eb).",
    )
    share_link_default_days = models.PositiveSmallIntegerField(
        "مدة صلاحية الروابط الافتراضية (بالأيام)",
        default=7,
        help_text="المدة الافتراضية لروابط مشاركة التقارير/ملفات الإنجاز لهذه المدرسة.",
    )
    allowed_academic_years = models.JSONField(
        "السنوات الدراسية المتاحة/المقبولة",
        default=list,
        blank=True,
        help_text="قائمة بالسنوات الدراسية (هجري) التي تظهر للمعلم عند إنشاء ملف إنجاز.",
    )
    current_academic_year = models.CharField(
        "السنة الدراسية الحالية (هجري)",
        max_length=9,
        blank=True,
        default="",
        help_text="مثال: 1447-1448. تُستخدم لتصنيف التقارير الجديدة وأرشفة السنوات.",
        db_index=True,
    )
    storage_used_bytes = models.PositiveBigIntegerField(
        "إجمالي التخزين المستخدم (بايت)",
        default=0,
        help_text="إجمالي تزايدي لحجم ملفات المدرسة (تقارير + ملفات إنجاز + شواهد). يُحدّث تلقائيًا.",
    )
    marketing_source = models.CharField(
        "مصدر التسجيل التسويقي",
        max_length=120,
        blank=True,
        default="",
    )
    marketing_medium = models.CharField(
        "وسيط الحملة",
        max_length=120,
        blank=True,
        default="",
    )
    marketing_campaign = models.CharField(
        "اسم الحملة",
        max_length=200,
        blank=True,
        default="",
    )
    marketing_content = models.CharField(
        "محتوى الإعلان",
        max_length=200,
        blank=True,
        default="",
    )
    marketing_term = models.CharField(
        "الكلمة التسويقية",
        max_length=200,
        blank=True,
        default="",
    )
    marketing_click_id = models.CharField(
        "معرف نقرة الإعلان",
        max_length=255,
        blank=True,
        default="",
    )
    marketing_referrer = models.CharField(
        "نطاق الإحالة",
        max_length=255,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField("أُنشئت في", auto_now_add=True)
    updated_at = models.DateTimeField("تم التحديث في", auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "مدرسة"
        verbose_name_plural = "المدارس"

    def __str__(self) -> str:
        return self.name or self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().lower()
        if self.current_academic_year:
            self.current_academic_year = _normalize_academic_year_hijri(self.current_academic_year)
        super().save(*args, **kwargs)


# =========================
# السنوات الدراسية (مرجع مركزي يديره مدير النظام)
# =========================
class AcademicYear(models.Model):
    """قائمة السنوات الدراسية الهجرية المتاحة على مستوى المنصة.

    يديرها مدير النظام من لوحة الآدمن، وتُستخدم كمصدر للخيارات في إعدادات المدرسة.
    """

    value = models.CharField("السنة الدراسية (هجري)", max_length=9, unique=True, db_index=True)
    is_active = models.BooleanField("نشطة (تظهر للمدارس)", default=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    created_at = models.DateTimeField("أُضيفت في", auto_now_add=True)

    class Meta:
        ordering = ("-value",)
        verbose_name = "سنة دراسية"
        verbose_name_plural = "السنوات الدراسية"

    def __str__(self) -> str:
        return self.value

    def clean(self):
        super().clean()
        self.value = _normalize_academic_year_hijri(self.value or "")
        _validate_academic_year_hijri(self.value)

    def save(self, *args, **kwargs):
        if self.value:
            self.value = _normalize_academic_year_hijri(self.value)
        super().save(*args, **kwargs)


# =========================
# مستخدم النظام: المعلم
# =========================
class TeacherManager(BaseUserManager):
    def create_user(self, phone, name, password=None, **extra_fields):
        if not phone:
            raise ValueError("رقم الجوال مطلوب")
        if not name:
            raise ValueError("اسم المستخدم مطلوب")
        user = self.model(phone=phone.strip(), name=name.strip(), **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(phone, name, password, **extra_fields)


class Teacher(AbstractBaseUser, PermissionsMixin):
    # Keep legacy imported identifiers intact during the SQLite → PostgreSQL
    # migration. New interactive entries remain constrained by the Saudi phone
    # validators in reports/forms.py.
    phone = models.CharField("رقم الجوال", max_length=64, unique=True)
    email = models.EmailField("البريد الإلكتروني", blank=True, default="")
    national_id = models.CharField("الهوية الوطنية", max_length=20, blank=True, null=True, unique=True)
    name = models.CharField("الاسم", max_length=150, db_index=True)

    # لاحقاً يمكن ربط المعلّم مباشرة بمدرسة افتراضية
    # school = models.ForeignKey(
    #     School,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     verbose_name="المدرسة",
    #     related_name="teachers",
    # )

    is_active = models.BooleanField("نشط", default=True)
    is_staff = models.BooleanField("موظّف لوحة", default=False)
    is_platform_admin = models.BooleanField("مشرف عام للمنصة؟", default=False)
    current_session_key = models.CharField(max_length=64, blank=True, default="")
    date_joined = models.DateTimeField("تاريخ الانضمام", auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    objects = TeacherManager()

    class Meta:
        verbose_name = "مستخدم (معلم)"
        verbose_name_plural = "المستخدمون"

    @property
    def display_role_label(self) -> str:
        """اسم الدور للعرض بالعربية (للواجهات).

        - المشرف العام (is_platform_admin) يجب أن يظهر بدوره الحقيقي المسجل في نطاق المشرف (PlatformAdminScope).
        - مدير المدرسة يجب أن يظهر كـ "مدير المدرسة" حتى لو كان is_staff=True.
        """
        active_school = None
        sid = None
        try:
            from ..middleware import get_current_request
            request = get_current_request()
            if request is not None:
                # ── إعادة استخدام ما حمّله الـ middleware ──
                active_school = getattr(request, "active_school", None)
                sid = request.session.get("active_school_id")
                if active_school is None and sid:
                    active_school = School.objects.filter(pk=sid, is_active=True).only("gender").first()
        except Exception:
            active_school = None
            sid = None
        try:
            from ..permissions import effective_user_role_label

            return effective_user_role_label(self, active_school=active_school, active_school_id=sid)
        except Exception:
            return "مستخدم"

    def save(self, *args, **kwargs):
        try:
            if bool(self.is_superuser):
                self.is_staff = True
        except Exception:
            pass
        super().save(*args, **kwargs)

    def __str__(self):
        role_name = self.display_role_label
        return f"{self.name} ({role_name or 'بدون دور'})"


class WebAuthnCredential(models.Model):
    """A passkey used for biometric login.

    Fingerprint/Face ID data remains on the user's device. The server stores
    only the public key and credential id needed to verify future sign-ins.
    """

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="webauthn_credentials",
        verbose_name="المستخدم",
    )
    credential_id = models.BinaryField("معرّف المفتاح", unique=True)
    credential_id_hash = models.CharField("بصمة معرّف المفتاح", max_length=64, unique=True, db_index=True)
    public_key_cose = models.BinaryField("المفتاح العام")
    sign_count = models.PositiveBigIntegerField("عداد التوقيع", default=0)
    device_name = models.CharField("اسم الجهاز", max_length=120, blank=True, default="")
    transports = models.JSONField("وسائل النقل", default=list, blank=True)
    is_active = models.BooleanField("نشط", default=True, db_index=True)
    last_used_at = models.DateTimeField("آخر استخدام", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإضافة", auto_now_add=True)

    class Meta:
        verbose_name = "مفتاح دخول بالبصمة"
        verbose_name_plural = "مفاتيح الدخول بالبصمة"
        indexes = [
            models.Index(fields=["teacher", "is_active"]),
        ]

    def __str__(self) -> str:
        label = self.device_name or "مفتاح دخول"
        return f"{label} - {self.teacher}"


# =========================
# نطاق مشرف عام للمنصة (عرض + تواصل فقط)
# =========================


class PlatformAdminRole(models.Model):
    """أدوار مشرفي المنصة (قابلة للإضافة/التعديل/الحذف من Django Admin)."""

    name = models.CharField("اسم الدور", max_length=64, unique=True)
    slug = models.SlugField("المعرّف (slug)", max_length=64, unique=True)
    is_active = models.BooleanField("نشط", default=True)
    order = models.PositiveIntegerField("ترتيب", default=0)

    class Meta:
        ordering = ("order", "id")
        verbose_name = "دور مشرف منصة"
        verbose_name_plural = "أدوار مشرفي المنصة"

    def __str__(self) -> str:
        return self.name


class PlatformAdminScope(models.Model):
    class GenderScope(models.TextChoices):
        ALL = "all", "الجميع"
        BOYS = "boys", "بنين"
        GIRLS = "girls", "بنات"

    admin = models.OneToOneField(
        Teacher,
        on_delete=models.CASCADE,
        related_name="platform_scope",
        verbose_name="المشرف العام",
    )

    role = models.ForeignKey(
        PlatformAdminRole,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="scopes",
        verbose_name="الدور",
        help_text="يمكن إدارة الأدوار (إضافة/تعديل/حذف) من Django Admin.",
    )
    gender_scope = models.CharField(
        "نطاق بنين/بنات",
        max_length=8,
        choices=GenderScope.choices,
        default=GenderScope.ALL,
    )
    allowed_cities = models.JSONField("المدن المسموحة", default=list, blank=True)
    allowed_schools = models.ManyToManyField(
        School,
        blank=True,
        related_name="platform_admins",
        verbose_name="مدارس محددة (اختياري)",
    )

    class Meta:
        verbose_name = "نطاق مشرف عام"
        verbose_name_plural = "نطاقات المشرفين العامين"

    def __str__(self) -> str:
        return f"Scope for {self.admin_id}"


# =========================
# تعليقات خاصة (يراها المعلم فقط)
# =========================
class TeacherPrivateComment(models.Model):
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="private_comments_received",
        verbose_name="المعلم المستهدف",
    )
    created_by = models.ForeignKey(
        Teacher,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments_created",
        verbose_name="أضيف بواسطة",
    )
    school = models.ForeignKey(
        School,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="المدرسة",
    )
    achievement_file = models.ForeignKey(
        "TeacherAchievementFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="ملف الإنجاز (اختياري)",
    )
    report = models.ForeignKey(
        "Report",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="التقرير (اختياري)",
    )
    body = models.TextField("التعليق")
    created_at = models.DateTimeField("تاريخ الإضافة", default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "تعليق خاص للمعلم"
        verbose_name_plural = "تعليقات خاصة للمعلمين"

    def __str__(self) -> str:
        return f"PrivateComment#{self.pk} to teacher#{self.teacher_id}"


# =========================
# مرجع الأقسام الديناميكي
# =========================
class Department(models.Model):
    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="departments",
        verbose_name="المدرسة",
        help_text="يظهر هذا القسم فقط داخل المدرسة المحددة.",
    )
    name = models.CharField("اسم القسم", max_length=120)
    slug = models.SlugField("المعرّف (slug)", max_length=64)
    role_label = models.CharField(
        "الاسم الظاهر في قائمة (الدور)",
        max_length=120,
        blank=True,
        help_text="هذا الاسم سيظهر كخيار (دور) عند إضافة المعلّم. إن تُرك فارغًا سيُستخدم اسم القسم.",
    )
    is_active = models.BooleanField("نشط", default=True)

    # ربط القسم بأنواع التقارير
    reporttypes = models.ManyToManyField(
        "ReportType",
        blank=True,
        related_name="departments",
        verbose_name="أنواع التقارير المرتبطة",
        help_text="اختَر الأنواع التي يحق لمسؤولي هذا القسم الاطلاع عليها (تُزامَن تلقائيًا مع دور القسم).",
    )

    class Meta:
        ordering = ("id",)
        constraints = [
            # ✅ السماح بتكرار slug بين المدارس المختلفة
            models.UniqueConstraint(
                fields=["school", "slug"],
                condition=models.Q(school__isnull=False),
                name="uniq_department_slug_per_school",
            ),
            # ✅ لو وُجدت أقسام عامة (school=NULL) تبقى فريدة عالميًا
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(school__isnull=True),
                name="uniq_global_department_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "slug"]),
        ]
        verbose_name = "قسم"
        verbose_name_plural = "الأقسام"

    def __str__(self):
        return self.name

    # ===== منع حذف قسم المدير الدائم =====
    def delete(self, *args, **kwargs):
        if self.slug == MANAGER_SLUG:
            raise ValidationError("لا يمكن حذف قسم المدير الدائم.")
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """تطبيع slug + فرض خصائص قسم المدير فقط.

        ملاحظة مهمة: كان هناك سابقًا مزامنة تلقائية بين Department.slug و Role.slug.
        هذا لا يعمل مع الأقسام المخصصة لكل مدرسة (لأن Role.slug فريد عالميًا)، لذلك تم إيقافه.
        """
        def _slugify_english(text: str) -> str:
            try:
                from unidecode import unidecode  # type: ignore

                text = unidecode(text or "")
            except Exception:
                pass
            return slugify(text or "", allow_unicode=False)

        if self.slug:
            self.slug = self.slug.strip().lower()
        else:
            self.slug = _slugify_english(self.name or "")

        # fallback: لا نسمح بـ slug فارغ
        if not self.slug:
            self.slug = "dept"

        if self.slug == MANAGER_SLUG:
            self.name = MANAGER_NAME
            self.role_label = MANAGER_ROLE_LABEL
            self.is_active = True

        if not self.role_label:
            self.role_label = self.name

        super().save(*args, **kwargs)


class DepartmentMembership(models.Model):
    TEACHER = "teacher"
    OFFICER = "officer"
    ROLE_TYPE_CHOICES = [(TEACHER, "Teacher"), (OFFICER, "Officer")]

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="القسم",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dept_memberships",
        verbose_name="المعلم",
    )
    role_type = models.CharField("نوع التكليف", max_length=16, choices=ROLE_TYPE_CHOICES, default=TEACHER)

    class Meta:
        unique_together = [("department", "teacher")]
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "تكليف قسم"
        verbose_name_plural = "تكليفات الأقسام"

    def __str__(self):
        return f"{self.teacher} @ {self.department} ({self.role_type})"

    # ===== ضمان: قسم المدير يقبل موظفين فقط =====
    def clean(self):
        super().clean()
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG and self.role_type != self.TEACHER:
            raise ValidationError("قسم المدير يقبل تكليف موظفين فقط (لا يوجد مسؤول قسم).")

    def save(self, *args, **kwargs):
        # إجبار الدور داخل القسم على TEACHER لقسم المدير
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG:
            self.role_type = self.TEACHER
        super().save(*args, **kwargs)


# =========================
# عضوية المدرسة (Teacher ↔ School)
# =========================
class SchoolMembership(models.Model):
    class RoleType(models.TextChoices):
        TEACHER = "teacher", "معلم"
        MANAGER = "manager", "مدير مدرسة"
        REPORT_VIEWER = "report_viewer", "مشرف تقارير (عرض فقط)"

    class JobTitle(models.TextChoices):
        TEACHER = "teacher", "معلم"
        ADMIN_STAFF = "admin_staff", "موظف إداري"
        LAB_TECH = "lab_tech", "محضر مختبر"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="المدرسة",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="school_memberships",
        verbose_name="المستخدم",
    )
    role_type = models.CharField(
        "الدور داخل المدرسة",
        max_length=16,
        choices=RoleType.choices,
        default=RoleType.TEACHER,
    )

    job_title = models.CharField(
        "المسمى الوظيفي داخل المدرسة",
        max_length=16,
        choices=JobTitle.choices,
        default=JobTitle.TEACHER,
        help_text="للعرض فقط داخل المدرسة (بنفس الصلاحيات).",
    )
    is_active = models.BooleanField("نشط؟", default=True)
    created_at = models.DateTimeField("أُنشئ في", auto_now_add=True)

    class Meta:
        unique_together = [("school", "teacher", "role_type")]
        constraints = [
            # مدرسة واحدة لا يمكن أن يكون لها أكثر من مدير نشط واحد
            models.UniqueConstraint(
                fields=["school"],
                # نستخدم القيمة النصية "manager" لتفادي NameError أثناء تعريف الكلاس
                condition=models.Q(role_type="manager", is_active=True),
                name="uniq_active_manager_per_school",
            )
        ]
        indexes = [
            models.Index(fields=["school"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "عضوية مدرسة"
        verbose_name_plural = "عضويات المدارس"

    def __str__(self) -> str:
        return f"{self.teacher} @ {self.school} ({self.role_type})"

    def save(self, *args, **kwargs):
        """فرض حد المعلمين حسب باقة المدرسة.

        المتطلبات:
        - لا يُحسب مدير المدرسة ضمن الحد (role_type=MANAGER).
        - الحد يُحسب على عدد حسابات المعلمين المرتبطين بالمدرسة (عضويات SchoolMembership بدور TEACHER)
          بغض النظر عن is_active.
        - الحذف يفتح مقعدًا (بما أنه يزيل العضوية).

        ملاحظة مهمة:
        - نطبق المنع فقط عند إنشاء عضوية TEACHER جديدة، أو عند تحويل/نقل عضوية إلى TEACHER/مدرسة أخرى.
          لا نمنع تحديثات بسيطة لعضوية موجودة (مثل تغيير is_active) حتى لو كانت المدرسة متجاوزة للحد تاريخيًا.
        """
        from django.core.exceptions import ValidationError

        should_enforce = self.pk is None
        if not should_enforce and self.pk is not None:
            try:
                prev = (
                    SchoolMembership.objects.filter(pk=self.pk)
                    .only("role_type", "school_id", "is_active")
                    .first()
                )
                if prev is not None and (
                    prev.role_type != self.role_type
                    or prev.school_id != self.school_id
                    or (not bool(prev.is_active) and bool(self.is_active))
                ):
                    should_enforce = True
            except Exception:
                # إن تعذرت المقارنة، لا نطبق المنع على تحديث عضوية موجودة
                should_enforce = False

        if should_enforce and self.role_type == self.RoleType.TEACHER:
            subscription = getattr(self.school, "subscription", None)
            if subscription is None or bool(getattr(subscription, "is_expired", True)):
                raise ValidationError("لا يوجد اشتراك فعّال لهذه المدرسة.")

            plan = getattr(subscription, "plan", None)
            max_teachers = int(getattr(plan, "max_teachers", 0) or 0)
            if max_teachers > 0:
                current_count = (
                    SchoolMembership.objects.filter(
                        school=self.school,
                        role_type=self.RoleType.TEACHER,
                    )
                    .exclude(pk=self.pk)
                    .count()
                )
                if current_count >= max_teachers:
                    raise ValidationError(f"لا يمكن إضافة أكثر من {max_teachers} معلّم لهذه المدرسة حسب الباقة.")

        # ✅ حد أقصى لمشرفي التقارير (عرض فقط): 2 نشطين لكل مدرسة
        if should_enforce and self.role_type == self.RoleType.REPORT_VIEWER and bool(self.is_active):
            active_viewers = (
                SchoolMembership.objects.filter(
                    school=self.school,
                    role_type=self.RoleType.REPORT_VIEWER,
                    is_active=True,
                )
                .exclude(pk=self.pk)
                .count()
            )
            if active_viewers >= 2:
                raise ValidationError("لا يمكن إضافة أكثر من 2 مشرف تقارير (عرض فقط) لهذه المدرسة.")

        return super().save(*args, **kwargs)


# =========================
# مرجع أنواع التقارير الديناميكي
# =========================
class ReportType(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="report_types",
        verbose_name="المدرسة",
        help_text="يظهر هذا النوع فقط في المدرسة المحددة.",
    )
    code = models.SlugField("الكود", max_length=40)
    name = models.CharField("الاسم", max_length=120)
    description = models.TextField("الوصف", blank=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("أُنشئ", auto_now_add=True)
    updated_at = models.DateTimeField("تحديث", auto_now=True)

    class Meta:
        ordering = ("order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"],
                condition=models.Q(school__isnull=False),
                name="uniq_reporttype_code_per_school",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(school__isnull=True),
                name="uniq_global_reporttype_code",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "code"]),
        ]
        verbose_name = "نوع تقرير"
        verbose_name_plural = "أنواع التقارير"

    def __str__(self) -> str:
        return self.name or self.code

    def save(self, *args, **kwargs):
        # تطبيع code إلى lowercase
        if self.code:
            self.code = self.code.strip().lower()
        super().save(*args, **kwargs)


# =========================
# نموذج التقرير العام
