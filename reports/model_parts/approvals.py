from __future__ import annotations

from .base import *
from .schools import School, Teacher

__all__ = [
    "ApprovalState",
    "ApprovalRoute",
    "ApprovalMixin",
    "ApprovalTransition",
]


class ApprovalState(models.TextChoices):
    """حالات دورة الاعتماد — مشتركة بين كل ما يُعتمد في المنصة.

    الحالات مستخرجة من ``TeacherAchievementFile`` الذي كان النمط الوحيد الناجح
    في المشروع، ومزيدةٌ بما يطلبه توصيف الأدوار: طبقة مراجعة وسيطة (الوكيل)،
    وحالة «بانتظار الاستكمال» التي تتكرر في مهام الأدوار الأربعة.

    لماذا ``NEEDS_INFO`` منفصلة عن ``RETURNED``؟ لأنهما رسالتان مختلفتان:
    «أعِد النظر في عملك» غير «أرفق ما نقص». دمجهما يجعل صاحب العمل يعيد كتابة
    ما لا يحتاج تعديلاً بحثاً عن خطأ لا وجود له.
    """

    DRAFT = "draft", "مسودة"
    SUBMITTED = "submitted", "مُرسل للمراجعة"
    UNDER_REVIEW = "under_review", "قيد المراجعة"
    NEEDS_INFO = "needs_info", "بانتظار استكمال البيانات"
    RECOMMENDED = "recommended", "موصى باعتماده"
    RETURNED = "returned", "مُعاد للملاحظة"
    APPROVED = "approved", "معتمد"


# الحالات التي يملك فيها صاحب العمل حقّ التعديل. ما عداها بيد المراجع، وتعديل
# صاحب العمل فيها يسحب من تحت المراجع ما يراجعه.
EDITABLE_STATES = frozenset(
    {ApprovalState.DRAFT, ApprovalState.RETURNED, ApprovalState.NEEDS_INFO}
)

# الحالة النهائية. ما بلغها لا يُعدَّل ولا يُحذف — والاعتماد الذي يمكن محوه
# ليس اعتماداً.
FINAL_STATES = frozenset({ApprovalState.APPROVED})

# الحالات التي ينتظر فيها العمل فعلاً من مراجع.
PENDING_REVIEW_STATES = frozenset(
    {ApprovalState.SUBMITTED, ApprovalState.UNDER_REVIEW, ApprovalState.RECOMMENDED}
)


class ApprovalRoute(models.TextChoices):
    """مسار الاعتماد — قابل للتهيئة لكل نوع تقرير.

    توصيف الأدوار ينصّ صراحةً على أن **لا يلزم مرور كل تقرير بالوكيل**، وأن
    المدير هو من يحدّد المسار بحسب نوع العمل. فتخزين المسار على النوع يحقّق
    ذلك بلا تفريع في الكود، ويجعل تغيير السياسة تعديل حقل لا نشر إصدار.
    """

    DIRECT = "direct", "مباشرةً إلى مدير المدرسة"
    VIA_DEPUTY = "via_deputy", "عبر الوكيل ثم مدير المدرسة"
    DEPUTY_FINAL = "deputy_final", "الوكيل يعتمد نهائياً"


class ApprovalMixin(models.Model):
    """حقول دورة الاعتماد، تُركَّب على أي كيان يُعتمد.

    **مكوّن لا نسخة.** النمط مطبَّق اليوم في ``TeacherAchievementFile`` وحده،
    والتوصيف يطلبه في ستة كيانات (التقارير، التكليفات، التعاميم، المحاضر،
    ملفات الأداء، الوثائق). كتابته ست مرات تعني ستة تعريفات تتباعد عند أول
    تعديل — وحالةٌ اسمها ``returned`` هنا و``rejected`` هناك تكفي لكسر أي شاشة
    موحّدة تُبنى فوقها لاحقاً.

    **المراجع غير المعتمِد.** حقلان منفصلان لا حقل واحد، لأن الوكيل يراجع
    والمدير يعتمد، ودمجهما يمحو أثر المراجعة الوسيطة تماماً — وهي بيت القصيد
    في دور الوكيل.
    """

    approval_state = models.CharField(
        "حالة الاعتماد",
        max_length=16,
        choices=ApprovalState.choices,
        default=ApprovalState.DRAFT,
        db_index=True,
    )
    submitted_at = models.DateTimeField("أُرسل في", null=True, blank=True)
    reviewed_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="راجعه",
    )
    reviewed_at = models.DateTimeField("رُوجع في", null=True, blank=True)
    decided_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="اعتمده",
    )
    decided_at = models.DateTimeField("تاريخ القرار", null=True, blank=True)
    review_note = models.TextField(
        "آخر ملاحظة",
        blank=True,
        default="",
        help_text="ملاحظة آخر إجراء — تُعرض لصاحب العمل. والسجل الكامل في الانتقالات.",
    )

    class Meta:
        abstract = True

    # ------------------------------------------------------------------
    # قراءات مشتقّة — لا تُخزَّن حتى لا تفترق عن الحالة
    # ------------------------------------------------------------------
    @property
    def is_editable_by_owner(self) -> bool:
        return self.approval_state in EDITABLE_STATES

    @property
    def is_final(self) -> bool:
        return self.approval_state in FINAL_STATES

    @property
    def is_pending_review(self) -> bool:
        return self.approval_state in PENDING_REVIEW_STATES

    @property
    def approval_tone(self) -> str:
        """نغمة العرض — تُترجَم في القالب إلى لون."""
        return {
            ApprovalState.DRAFT: "draft",
            ApprovalState.SUBMITTED: "pending",
            ApprovalState.UNDER_REVIEW: "pending",
            ApprovalState.NEEDS_INFO: "attention",
            ApprovalState.RECOMMENDED: "recommended",
            ApprovalState.RETURNED: "attention",
            ApprovalState.APPROVED: "approved",
        }.get(self.approval_state, "draft")


class ApprovalTransition(models.Model):
    """سجل انتقالات الاعتماد — واقعة لكل تغيّر حالة.

    **غير قابل للتعديل.** على غرار ``AuditLog``: ما جرى جرى. والفرق بين هذا
    السجل وذاك أن ``AuditLog`` يرصد *أن* السجل تغيّر، وهذا يرصد *كيف* تدرّج
    العمل ومَن قرّر ولماذا — وهو ما يُسأل عنه عند أي نزاع.

    **``acted_as`` هو بيت القصيد.** إجراء يُنفَّذ بتفويض يُنسب لمنفّذه وللمفوِّض
    معاً، فتبقى المسؤولية مقروءة بعد انقضاء مدة التفويض. وبدون هذا الحقل يصير
    التفويض منحةً دائمة لا أثر لها.

    **عام لا خاص بالتقرير.** المرجع ``content_type`` + ``object_id`` ليخدم
    الكيانات الستة بجدول واحد، فيُبنى «صندوق المراجعة» مرة واحدة لا ست مرات.
    """

    class Action(models.TextChoices):
        SUBMIT = "submit", "إرسال للمراجعة"
        START_REVIEW = "start_review", "بدء المراجعة"
        REQUEST_INFO = "request_info", "طلب استكمال"
        RETURN = "return", "إعادة للملاحظة"
        RECOMMEND = "recommend", "توصية بالاعتماد"
        APPROVE = "approve", "اعتماد"
        WITHDRAW = "withdraw", "سحب للتعديل"

    class ActedAs(models.TextChoices):
        SELF = "self", "بالأصالة"
        DELEGATE = "delegate", "بالنيابة"

    content_type = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.CASCADE,
        verbose_name="نوع السجل",
    )
    object_id = models.PositiveIntegerField("معرّف السجل", db_index=True)

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="approval_transitions",
        verbose_name="المدرسة",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_transitions",
        verbose_name="المنفّذ",
    )
    actor_name = models.CharField(
        "اسم المنفّذ (وقت الحدث)", max_length=150, blank=True, default=""
    )
    actor_role = models.CharField(
        "دور المنفّذ (وقت الحدث)", max_length=64, blank=True, default=""
    )
    acted_as = models.CharField(
        "صفة التنفيذ",
        max_length=16,
        choices=ActedAs.choices,
        default=ActedAs.SELF,
    )
    on_behalf_of = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_transitions_by_proxy",
        verbose_name="نيابةً عن",
    )

    action = models.CharField("الإجراء", max_length=20, choices=Action.choices)
    from_state = models.CharField("من حالة", max_length=16, blank=True, default="")
    to_state = models.CharField("إلى حالة", max_length=16, blank=True, default="")
    note = models.TextField("الملاحظة", blank=True, default="")
    created_at = models.DateTimeField("وقت الإجراء", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["content_type", "object_id", "-created_at"]),
            models.Index(fields=["school", "-created_at"]),
        ]
        verbose_name = "انتقال اعتماد"
        verbose_name_plural = "انتقالات الاعتماد"

    def __str__(self) -> str:
        return f"{self.actor_display} · {self.get_action_display()}"

    @property
    def actor_display(self) -> str:
        snapshot = (self.actor_name or "").strip()
        if snapshot:
            return snapshot
        if self.actor_id:
            return (getattr(self.actor, "name", "") or "").strip() or "مستخدم"
        return "حساب محذوف"

    @property
    def by_proxy(self) -> bool:
        return self.acted_as == self.ActedAs.DELEGATE

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("سجل الانتقالات لا يُعدّل بعد كتابته.")
        if self.actor_id and not self.actor_name:
            try:
                self.actor_name = (getattr(self.actor, "name", "") or "")[:150]
            except Exception:
                pass
        return super().save(*args, **kwargs)
