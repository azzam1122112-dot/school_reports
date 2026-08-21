from __future__ import annotations

from .approvals import ApprovalMixin
from .base import *
from .schools import School, Teacher
from .reports import Report

class TeacherAchievementFile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "مسودة"
        SUBMITTED = "submitted", "بانتظار الاعتماد"
        RETURNED = "returned", "مُعاد للمعلّم"
        APPROVED = "approved", "معتمد"

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="achievement_files",
        verbose_name="المعلّم",
        db_index=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="achievement_files",
        verbose_name="المدرسة",
        db_index=True,
    )

    academic_year = models.CharField(
        "السنة الدراسية (هجري)",
        max_length=9,
        help_text="مثال: 1447-1448",
        db_index=True,
    )
    status = models.CharField(
        "الحالة",
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    submitted_at = models.DateTimeField("تاريخ الإرسال", null=True, blank=True)
    decided_at = models.DateTimeField("تاريخ القرار", null=True, blank=True)
    decided_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="achievement_files_decided",
        verbose_name="اعتماد بواسطة",
    )

    # Snapshot بيانات عامة (تظهر في PDF)
    teacher_name = models.CharField("اسم المعلّم", max_length=150, blank=True, default="")
    teacher_phone = models.CharField("رقم الجوال", max_length=64, blank=True, default="")
    school_name = models.CharField("اسم المدرسة", max_length=200, blank=True, default="")
    school_stage = models.CharField("المرحلة", max_length=32, blank=True, default="")

    # بيانات عامة تُعبّأ سنويًا (مع زر استيراد)
    qualifications = models.TextField("المؤهلات", blank=True, default="")
    professional_experience = models.TextField("الخبرات المهنية", blank=True, default="")
    specialization = models.TextField("التخصص", blank=True, default="")
    teaching_load = models.TextField("نصاب الحصص", blank=True, default="")
    subjects_taught = models.TextField("مواد التدريس", blank=True, default="")
    contact_info = models.TextField("بيانات التواصل", blank=True, default="")

    manager_notes = models.TextField("ملاحظات مدير المدرسة", blank=True, default="")

    pdf_file = models.FileField(
        "ملف PDF",
        upload_to=_achievement_pdf_upload_to,
        storage=PublicRawMediaStorage(),
        blank=True,
        null=True,
        validators=[validate_pdf_file],
    )
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)
    pdf_generated_at = models.DateTimeField("آخر توليد PDF", null=True, blank=True)

    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        verbose_name = "ملف إنجاز"
        verbose_name_plural = "ملفات الإنجاز"
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "school", "academic_year"],
                name="uniq_teacher_achievement_per_year",
            )
        ]
        indexes = [
            models.Index(fields=["school", "academic_year", "status"]),
            models.Index(fields=["teacher", "academic_year"]),
        ]

    def clean(self):
        self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        _validate_academic_year_hijri(self.academic_year)
        return super().clean()

    def save(self, *args, **kwargs):
        # snapshot تلقائي (لا يعتمد على إدخال المستخدم)
        try:
            self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        except Exception:
            pass
        try:
            if self.teacher_id:
                self.teacher_name = self.teacher_name or getattr(self.teacher, "name", "") or ""
                self.teacher_phone = self.teacher_phone or getattr(self.teacher, "phone", "") or ""
        except Exception:
            pass
        try:
            if self.school_id:
                self.school_name = self.school_name or getattr(self.school, "name", "") or ""
                self.school_stage = self.school_stage or getattr(self.school, "stage", "") or ""
        except Exception:
            pass
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.teacher_name or self.teacher_id} - {self.academic_year}"


class AchievementSection(models.Model):
    class Code(models.IntegerChoices):
        SECTION_1 = 1, "1- أداء الواجبات الوظيفية"
        SECTION_2 = 2, "2- التفاعل مع المجتمع المهني"
        SECTION_3 = 3, "3- التفاعل مع أولياء الأمور"
        SECTION_4 = 4, "4- التنوع في استراتيجيات التدريس"
        SECTION_5 = 5, "5- تحسين نتائج المتعلمين"
        SECTION_6 = 6, "6- إعداد وتنفيذ خطة التعلم"
        SECTION_7 = 7, "7- توظيف تقنيات ووسائل التعلم المناسبة"
        SECTION_8 = 8, "8- تهيئة بيئة تعليمية"
        SECTION_9 = 9, "9- الإدارة الصفية"
        SECTION_10 = 10, "10- تحليل نتائج المتعلمين وتشخيص مستوياتهم"
        SECTION_11 = 11, "11- تنوع أساليب التقويم"

    file = models.ForeignKey(
        TeacherAchievementFile,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name="ملف الإنجاز",
        db_index=True,
    )
    code = models.PositiveSmallIntegerField("المحور", choices=Code.choices)
    title = models.CharField("العنوان", max_length=200, blank=True, default="")
    teacher_notes = models.TextField("ملاحظات المعلّم", blank=True, default="")

    class Meta:
        verbose_name = "محور ملف إنجاز"
        verbose_name_plural = "محاور ملفات الإنجاز"
        constraints = [
            models.UniqueConstraint(fields=["file", "code"], name="uniq_achievement_section_per_file")
        ]
        ordering = ["code", "id"]

    def save(self, *args, **kwargs):
        if not self.title:
            try:
                self.title = dict(self.Code.choices).get(int(self.code), "")
            except Exception:
                pass
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.file_id} - {self.code}"


class AchievementEvidenceImage(models.Model):
    section = models.ForeignKey(
        AchievementSection,
        on_delete=models.CASCADE,
        related_name="evidence_images",
        verbose_name="المحور",
        db_index=True,
    )
    image = models.ImageField(
        "صورة الشاهد",
        upload_to=_achievement_evidence_upload_to,
        validators=[validate_image_file],
    )
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "صورة شاهد"
        verbose_name_plural = "صور الشواهد"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"EvidenceImage #{self.pk} (section {self.section_id})"

def _achievement_report_evidence_upload_to(instance: "AchievementEvidenceReport", filename: str) -> str:
    """Archive storage for frozen report images inside achievement file.

    Keeps evidence independent from original report media, so deleting a report
    won't break the achievement file PDF.
    """

    achievement_file = instance.section.file
    file_id = instance.section.file_id or "file"
    section_code = instance.section.code or "sec"
    ev_id = instance.pk or "new"
    return school_file_path(
        achievement_file.school,
        "achievements/report-evidence",
        filename,
        parts=(f"file-{file_id}", f"section-{section_code}", f"evidence-{ev_id}"),
        fallback="evidence",
    )


class AchievementEvidenceReport(models.Model):
    """Link a teacher report as an evidence inside an achievement section.

    - In DRAFT/RETURNED: it behaves like a live link to the report.
    - On SUBMITTED: it is frozen (snapshot + archived images) to keep the
      achievement file stable even if the source report is deleted.
    """

    section = models.ForeignKey(
        AchievementSection,
        on_delete=models.CASCADE,
        related_name="evidence_reports",
        verbose_name="المحور",
        db_index=True,
    )

    report = models.ForeignKey(
        "Report",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="achievement_evidences",
        verbose_name="التقرير",
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    frozen_at = models.DateTimeField("تاريخ التجميد", null=True, blank=True)
    frozen_data = models.JSONField("بيانات التقرير (Snapshot)", blank=True, default=dict)

    archived_image1 = models.ImageField(
        "صورة 1 (مؤرشفة)",
        upload_to=_achievement_report_evidence_upload_to,
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    archived_image2 = models.ImageField(
        "صورة 2 (مؤرشفة)",
        upload_to=_achievement_report_evidence_upload_to,
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    archived_image3 = models.ImageField(
        "صورة 3 (مؤرشفة)",
        upload_to=_achievement_report_evidence_upload_to,
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    archived_image4 = models.ImageField(
        "صورة 4 (مؤرشفة)",
        upload_to=_achievement_report_evidence_upload_to,
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "تقرير شاهد"
        verbose_name_plural = "تقارير الشواهد"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["section", "report"], name="uniq_achievement_section_report_evidence")
        ]

    def __str__(self) -> str:
        rid = getattr(self, "report_id", None)
        return f"{self.section_id} - report:{rid}"

    @property
    def is_frozen(self) -> bool:
        return bool(self.frozen_at)


class SchoolLeadershipPortfolio(ApprovalMixin):
    """ملف الأداء القيادي لمدير المدرسة.

    **كان يعتمده صاحبه بنفسه.** قبل هذا التعديل كانت حالته ``DRAFT``/``COMPLETED``
    يضبطها المدير على ملفه — وهو خرقٌ صريح لقاعدة يكرّرها توصيف الأدوار في
    موضعين: «لا يعتمد ملف أدائه الشخصي بنفسه».

    والإصلاح يستعمل التمييز الذي بُني للمحاضر: مدرسةٌ داخل مجموعة لها مدير
    تنفيذي يُرسَل ملفُها إليه فيراجعه ويعتمده. ومدرسةٌ مستقلة لا سلطة فوق
    مديرها فيها، فيُصدر ملفه إصداراً — وهو ما تفعله ``allows_issuance``.
    فالقاعدة تُطبَّق حيث لها معنى، ولا تُعطِّل ملفاً لا مراجع له.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "قيد الإعداد"
        COMPLETED = "completed", "مكتمل"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="leadership_portfolios",
        verbose_name="المدرسة",
    )
    manager = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name="leadership_portfolios",
        verbose_name="مدير المدرسة",
    )
    academic_year = models.CharField("السنة الدراسية (هجري)", max_length=9, db_index=True)
    status = models.CharField(
        "الحالة",
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    manager_name = models.CharField("اسم المدير", max_length=150, blank=True, default="")
    school_name = models.CharField("اسم المدرسة", max_length=200, blank=True, default="")
    leadership_vision = models.TextField("الرؤية القيادية", blank=True, default="")
    executive_summary = models.TextField("الملخص التنفيذي", blank=True, default="")
    notable_achievements = models.TextField("أبرز المنجزات", blank=True, default="")
    improvement_priorities = models.TextField("أولويات التحسين", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ملف الأداء القيادي"
        verbose_name_plural = "ملفات الأداء القيادي"
        constraints = [
            models.UniqueConstraint(
                fields=["school", "academic_year"],
                name="uniq_school_leadership_portfolio_per_year",
            )
        ]
        ordering = ["-academic_year", "-id"]

    def clean(self):
        self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        _validate_academic_year_hijri(self.academic_year)
        return super().clean()

    def save(self, *args, **kwargs):
        self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        if self.manager_id:
            self.manager_name = self.manager_name or getattr(self.manager, "name", "") or ""
        if self.school_id:
            self.school_name = self.school_name or getattr(self.school, "name", "") or ""
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.school_name or self.school_id} - {self.academic_year}"

    # ------------------------------------------------------------------
    # الاعتماد: من فوق مدير المدرسة في هذا الملف؟
    # ------------------------------------------------------------------
    def _executive_director(self):
        """المدير التنفيذي لمجموعة هذه المدرسة، إن وُجد."""
        group_id = getattr(self.school, "group_id", None)
        if not group_id:
            return None
        from .schools import SchoolGroupMembership

        membership = (
            SchoolGroupMembership.objects.filter(
                group_id=group_id,
                role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR,
                is_active=True,
            )
            .select_related("user")
            .first()
        )
        return getattr(membership, "user", None)

    def _is_owner(self, user) -> bool:
        return user is not None and self.manager_id == getattr(user, "pk", None)

    def can_review_approval(self, user, school):
        """المدير التنفيذي يراجع ملفات مديري مدارسه — بند صريح في توصيفه.

        ولا يستمد ذلك من عضوية مدرسية؛ فهو لا يملكها. ومن سواه لا يراجع هذا
        الملف: ليس عملاً إدارياً يقع في نطاق وكيل، بل تقويمٌ لقيادة المدرسة.
        """
        director = self._executive_director()
        if director is not None and getattr(user, "pk", None) == director.pk:
            return True
        return False

    def can_finalize_approval(self, user, school):
        director = self._executive_director()
        if director is not None and getattr(user, "pk", None) == director.pk:
            return True
        # مدير المدرسة ليس معتمِداً لملفه — والقاعدة العامة كانت ستمنحه ذلك
        # لأنه مدير المدرسة. فنقطعها هنا صراحةً.
        if self._is_owner(user):
            return False
        return None

    def allows_issuance(self, user, school) -> bool:
        """مدرسةٌ مستقلة: لا سلطة فوق مديرها في ملفه، فيُصدره إصداراً.

        وما دامت المدرسة في مجموعة لها مدير تنفيذي، فالإصدار مغلق والمسار
        الطبيعي هو الإرسال إليه — وذلك ما يجعل القاعدة تُطبَّق حيث لها معنى
        دون أن تُعطِّل ملفاً لا مراجع له.
        """
        if not self._is_owner(user):
            return False
        return self._executive_director() is None

    def assert_ready_for_submission(self) -> None:
        if not self.sections.filter(is_completed=True).exists():
            raise ValidationError("أكمل محوراً واحداً على الأقل قبل إرسال الملف.")


class LeadershipPortfolioSection(models.Model):
    class Code(models.IntegerChoices):
        PLANNING = 1, "التخطيط والتشغيل المدرسي"
        LEARNING_OUTCOMES = 2, "تحسين نواتج التعلم"
        PEOPLE_LEADERSHIP = 3, "قيادة الكادر والتنمية المهنية"
        SCHOOL_ENVIRONMENT = 4, "البيئة المدرسية والانضباط والسلامة"
        GOVERNANCE = 5, "الحوكمة واللجان وإدارة الموارد"
        COMMUNITY = 6, "الشراكة الأسرية والمجتمعية"
        DIGITAL_TRANSFORMATION = 7, "التحول الرقمي والابتكار"
        INITIATIVES = 8, "المبادرات والمنجزات النوعية"

    portfolio = models.ForeignKey(
        SchoolLeadershipPortfolio,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name="ملف الأداء القيادي",
    )
    code = models.PositiveSmallIntegerField("المحور", choices=Code.choices)
    notes = models.TextField("وصف الممارسات والشواهد", blank=True, default="")
    is_completed = models.BooleanField("مكتمل", default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "محور أداء قيادي"
        verbose_name_plural = "محاور الأداء القيادي"
        constraints = [
            models.UniqueConstraint(
                fields=["portfolio", "code"],
                name="uniq_leadership_section_per_portfolio",
            )
        ]
        ordering = ["code", "id"]

    def __str__(self) -> str:
        return f"{self.portfolio_id} - {self.get_code_display()}"


class LeadershipEvidenceImage(models.Model):
    section = models.ForeignKey(
        LeadershipPortfolioSection,
        on_delete=models.CASCADE,
        related_name="evidence_images",
        verbose_name="المحور",
    )
    image = models.ImageField(
        "صورة الشاهد",
        upload_to=_leadership_evidence_upload_to,
        validators=[validate_image_file],
    )
    caption = models.CharField("وصف الشاهد", max_length=180, blank=True, default="")
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "شاهد أداء قيادي"
        verbose_name_plural = "شواهد الأداء القيادي"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Leadership evidence #{self.pk}"


class LeadershipEvidenceReport(models.Model):
    """A school manager report used as evidence in a leadership section."""

    section = models.ForeignKey(
        LeadershipPortfolioSection,
        on_delete=models.CASCADE,
        related_name="evidence_reports",
        verbose_name="المحور",
        db_index=True,
    )
    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="leadership_evidences",
        verbose_name="التقرير",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تقرير شاهد أداء قيادي"
        verbose_name_plural = "تقارير شواهد الأداء القيادي"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "report"],
                name="uniq_leadership_section_report_evidence",
            )
        ]

    def __str__(self) -> str:
        return f"{self.section_id} - report:{self.report_id}"

__all__ = [name for name in globals() if not name.startswith("__")]
