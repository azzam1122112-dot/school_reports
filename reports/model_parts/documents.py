from __future__ import annotations

from .approvals import ApprovalMixin
from .base import *
from .schools import Department, School, Teacher

__all__ = ["Document"]


def _document_upload_to(instance, filename: str) -> str:
    year = (getattr(instance, "academic_year", "") or "unknown").replace("/", "-")
    school_id = getattr(instance, "school_id", None) or "school"
    return f"documents/{school_id}/{year}/{_safe_unique_filename(filename, fallback='document')}"


class Document(ApprovalMixin):
    """وثيقة مدرسية — تُرفع وتُصنَّف ثم يُعتمد نقلها إلى الأرشيف.

    **الأرشفة قرار لا فعل رفع.** التوصيف يجعل «رفع الملفات وتصنيفها» من مهام
    الموظف الإداري، و«اعتماد نقل الوثائق إلى الأرشيف» من مهام مدير المدرسة —
    وهما مرحلتان لا واحدة. ولذلك ترث الوثيقة ``ApprovalMixin``: تُرفع مسودةً،
    ثم تُرسَل للأرشفة، ثم تُعتمد أو تُعاد بملاحظة. وما اعتُمد صار أرشيفاً لا
    يُعدَّل ولا يُحذف.

    **التصنيف ثلاثي كما ينصّ التوصيف**: السنة والقسم والنوع. وثلاثتها معاً هي
    ما يجعل البحث ممكناً في مدرسة تراكم عليها آلاف الوثائق — فمحورٌ واحد يترك
    الباحث يقلّب مئات النتائج.

    والفرق عن ``SchoolYearArchive``: ذاك لقطة ZIP تلقائية لسنة كاملة، وهذه
    وثيقةٌ مفردة يرفعها إنسان ويصنّفها ويُعتمد إدراجها. الأول نسخة احتياطية،
    والثانية أرشيف عمل.
    """

    class Kind(models.TextChoices):
        """نوع الوثيقة.

        قائمة في الكود لا جدول في القاعدة: أنواع الوثائق الإدارية مستقرّة عبر
        المدارس بخلاف أنواع التقارير التي تختلف باختلاف برامج كل مدرسة. وجدولٌ
        لكل مدرسة هنا يعني شاشة إدارة إضافية لقائمة لا تكاد تتغيّر.
        """

        MINUTES = "minutes", "محضر"
        CIRCULAR = "circular", "تعميم أو خطاب"
        LETTER = "letter", "مخاطبة رسمية"
        REPORT = "report", "تقرير"
        FORM = "form", "نموذج أو استمارة"
        FINANCIAL = "financial", "مستند مالي"
        CONTRACT = "contract", "عقد أو اتفاقية"
        CERTIFICATE = "certificate", "شهادة أو خطاب شكر"
        OTHER = "other", "أخرى"

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="documents",
        verbose_name="المدرسة", db_index=True,
    )
    uploaded_by = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents_uploaded", verbose_name="رافع الوثيقة",
    )
    # ``owner`` هو ما يقرؤه مكوّن الاعتماد ليعرف صاحب العمل. مستقل عن
    # ``uploaded_by`` الذي قد يُفرَّغ عند حذف الحساب: صاحبُ الوثيقة يجب أن يبقى
    # معروفاً للمكوّن حتى بعد رحيل الحساب، وإلا صارت الوثيقة بلا مُرسِل.
    owner = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="documents_owned",
        verbose_name="صاحب الوثيقة", db_index=True,
    )
    owner_name = models.CharField(
        "اسم الرافع (وقت الرفع)", max_length=150, blank=True, default=""
    )

    title = models.CharField("عنوان الوثيقة", max_length=200, db_index=True)
    description = models.TextField("وصف مختصر", blank=True, default="")

    # ── محاور التصنيف الثلاثة ────────────────────────────────────────────
    academic_year = models.CharField(
        "السنة الدراسية (هجري)", max_length=9, blank=True, default="", db_index=True
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents", verbose_name="القسم",
    )
    kind = models.CharField(
        "نوع الوثيقة", max_length=16, choices=Kind.choices,
        default=Kind.OTHER, db_index=True,
    )

    file = models.FileField(
        "الملف",
        upload_to=_document_upload_to,
        storage=PublicRawMediaStorage(),
        validators=[
            FileExtensionValidator(
                ["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"]
            ),
            validate_attachment_file,
        ],
        help_text="PDF أو صورة أو مستند حتى 5MB.",
    )
    storage_bytes = models.PositiveBigIntegerField(default=0, editable=False)

    created_at = models.DateTimeField("رُفعت في", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["school", "-created_at"]),
            models.Index(fields=["school", "approval_state"]),
            # محاور البحث الثلاثة مجتمعة — وهي كيف تُقرأ الشاشة فعلاً.
            models.Index(fields=["school", "academic_year", "kind"]),
            models.Index(fields=["owner", "-created_at"]),
        ]
        verbose_name = "وثيقة"
        verbose_name_plural = "الوثائق"

    def __str__(self) -> str:
        return self.title

    # ------------------------------------------------------------------
    @property
    def is_archived(self) -> bool:
        """في الأرشيف = اعتُمد نقلها إليه."""
        return self.is_final

    @property
    def size_kb(self) -> int:
        return round((self.storage_bytes or 0) / 1024)

    def assert_ready_for_submission(self) -> None:
        if not getattr(self.file, "name", ""):
            raise ValidationError("أرفق ملف الوثيقة قبل إرسالها للأرشفة.")
        if not (self.academic_year or "").strip():
            raise ValidationError("حدّد السنة الدراسية — الأرشيف يُقلَّب بالسنة أولاً.")

    def allows_issuance(self, user, school) -> bool:
        """Let a school manager issue their own authoritative document.

        This is deliberately *issuance*, not self-approval.  The shared
        approval service records the distinction in the immutable timeline,
        while preventing a manager-owned document from waiting forever for a
        reviewer that does not exist above the school manager.
        """
        from ..permissions import is_school_manager

        return (
            self.owner_id == getattr(user, "pk", None)
            and self.school_id == getattr(school, "pk", None)
            and is_school_manager(user, active_school=school)
        )

    def can_review_approval(self, user, school):
        """من يعتمد نقل الوثيقة إلى الأرشيف.

        مدير المدرسة (يُحسم قبل الوصول إلى هنا)، ثم من مُنح ``archive_documents``
        **وكان قسم الوثيقة في نطاقه**. ووثيقةٌ بلا قسم لا يراجعها إلا المدير —
        فلا نطاق يشملها.
        """
        from ..capabilities import ARCHIVE_DOCUMENTS
        from ..permissions import capability_source, supervised_department_ids

        if capability_source(user, ARCHIVE_DOCUMENTS, school) is None:
            return False
        if self.department_id is None:
            return False
        return self.department_id in supervised_department_ids(user, school)

    def save(self, *args, **kwargs):
        if self.owner_id and not self.owner_name:
            try:
                self.owner_name = (getattr(self.owner, "name", "") or "")[:150]
            except Exception:
                pass
        # ``storage_bytes`` تملؤه إشارات ``storage_tracking`` كبقية النماذج ذات
        # الملفات: تقرأ الحجم عند تغيّر الملف فقط، فلا يُستدعى التخزين شبكياً في
        # كل حفظ اعتماد أو تعديل وصف.
        return super().save(*args, **kwargs)
