from __future__ import annotations

from .approvals import ApprovalMixin, ApprovalState
from .base import *
from .schools import Department, School, SchoolGroup, Teacher

__all__ = ["Plan", "PlanGoal", "PlanTask", "Initiative"]


class Plan(ApprovalMixin):
    """خطة — مدرسية أو تحسين مشتركة على مستوى المجموعة.

    **الخطة وثيقة تُعتمد لا مجرد قائمة نوايا.** ترث ``ApprovalMixin`` لأن
    توصيف الأدوار يجعل إعدادها من مهام المدير واعتمادها جزءاً من دورة العمل —
    وخطةٌ تسري بلا اعتماد تجعل كل مراجعة لاحقة لها بلا مرجع تُقاس إليه.

    **مستويان بنموذج واحد** — كما التكليف والاجتماع. وخطة التحسين المشتركة ليست
    نوعاً آخر من الخطط بل خطةٌ نطاقُها المجموعة.

    **التنفيذ لا يعيش هنا.** مهام الخطة تتحوّل إلى تكليفات، فتُتابَع بالمواعيد
    والشواهد والاعتماد التي بُنيت لها — ولا تُبنى لها متابعة ثانية موازية
    تتباعد عنها.
    """

    class Scope(models.TextChoices):
        SCHOOL = "school", "خطة مدرسة"
        GROUP = "group", "خطة تحسين مشتركة"

    class Stage(models.TextChoices):
        PREPARING = "preparing", "قيد الإعداد"
        RUNNING = "running", "قيد التنفيذ"
        CLOSED = "closed", "مُغلقة"

    scope = models.CharField(
        "نطاق الخطة", max_length=16, choices=Scope.choices,
        default=Scope.SCHOOL, db_index=True,
    )
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, null=True, blank=True,
        related_name="plans", verbose_name="المدرسة",
    )
    group = models.ForeignKey(
        SchoolGroup, on_delete=models.CASCADE, null=True, blank=True,
        related_name="plans", verbose_name="المجموعة",
    )
    owner = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="plans_owned", verbose_name="مُعِدّ الخطة",
    )
    owner_name = models.CharField(
        "اسم المُعِدّ (وقت الإنشاء)", max_length=150, blank=True, default=""
    )

    title = models.CharField("عنوان الخطة", max_length=200)
    description = models.TextField("وصف الخطة", blank=True, default="")
    academic_year = models.CharField(
        "السنة الدراسية (هجري)", max_length=9, blank=True, default="", db_index=True
    )
    starts_on = models.DateField("تبدأ في", null=True, blank=True)
    ends_on = models.DateField("تنتهي في", null=True, blank=True)
    stage = models.CharField(
        "مرحلة التنفيذ", max_length=16, choices=Stage.choices,
        default=Stage.PREPARING, db_index=True,
    )

    created_at = models.DateTimeField("أُنشئت في", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["school", "-created_at"]),
            models.Index(fields=["group", "-created_at"]),
        ]
        verbose_name = "خطة"
        verbose_name_plural = "الخطط"

    def __str__(self) -> str:
        return self.title

    # ------------------------------------------------------------------
    @property
    def progress_percent(self) -> int:
        """نسبة إنجاز الخطة = مهامها المنجَزة ÷ مهامها.

        محسوبة من المهام لا مدخلة يدوياً: رقمٌ يكتبه صاحب الخطة عن خطته يقيس
        تفاؤله لا تنفيذها.
        """
        tasks = list(self.tasks.all())
        if not tasks:
            return 0
        done = sum(1 for task in tasks if task.is_done)
        return round(done * 100 / len(tasks))

    @property
    def task_summary(self) -> dict:
        tasks = list(self.tasks.all())
        return {
            "total": len(tasks),
            "done": sum(1 for task in tasks if task.is_done),
            "tracked": sum(1 for task in tasks if task.assignment_id),
            "late": sum(1 for task in tasks if task.is_late),
        }

    def _is_owner(self, user) -> bool:
        return user is not None and self.owner_id == getattr(user, "pk", None)

    def can_finalize_approval(self, user, school):
        """خطة المجموعة يعتمدها مُعِدّها — فلا سلطة فوقه فيها."""
        if self.scope == self.Scope.GROUP and self._is_owner(user):
            return True
        return None

    def allows_issuance(self, user, school) -> bool:
        """المدير التنفيذي يُصدر خطة مجموعته، ومدير المدرسة يُصدر خطة مدرسته.

        كلاهما صاحب الوثيقة وصاحب سلطتها معاً — والفرق عن الاعتماد مشروح في
        ``services_approval.issue``. أما خطةٌ يُعدّها وكيل أو موظف فتمر
        بالمراجعة كما يمر أي عمل يُرفع لمن فوقه.
        """
        if not self._is_owner(user):
            return False
        if self.scope == self.Scope.GROUP:
            return True
        from ..permissions import is_school_manager

        return is_school_manager(user, active_school=self.school)

    def assert_ready_for_submission(self) -> None:
        if not self.tasks.exists():
            raise ValidationError("خطة بلا مهام لا تُنفَّذ — أضف مهمة واحدة على الأقل.")

    def clean(self):
        super().clean()
        if self.scope == self.Scope.SCHOOL and self.school_id is None:
            raise ValidationError({"school": "خطة المدرسة تحتاج مدرسة."})
        if self.scope == self.Scope.GROUP and self.group_id is None:
            raise ValidationError({"group": "الخطة المشتركة تحتاج مجموعة."})
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "نهاية الخطة قبل بدايتها."})

    def save(self, *args, **kwargs):
        if self.owner_id and not self.owner_name:
            try:
                self.owner_name = (getattr(self.owner, "name", "") or "")[:150]
            except Exception:
                pass
        return super().save(*args, **kwargs)


class PlanGoal(models.Model):
    """هدف في الخطة، بمؤشر قياسه.

    المؤشر نصٌّ حر عمداً: مؤشرات المدارس تُصاغ بلغتها التنظيمية ولا تُختزل في
    قائمة مغلقة. والقياس الفعلي يقع على المهام المرتبطة بالهدف لا على رقمٍ
    يُدخَل يدوياً.
    """

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="goals", verbose_name="الخطة"
    )
    order = models.PositiveSmallIntegerField("الترتيب", default=1)
    title = models.CharField("الهدف", max_length=255)
    indicator = models.CharField("مؤشر القياس", max_length=255, blank=True, default="")
    target = models.CharField("المستهدف", max_length=120, blank=True, default="")

    class Meta:
        ordering = ("order", "id")
        verbose_name = "هدف خطة"
        verbose_name_plural = "أهداف الخطط"

    def __str__(self) -> str:
        return self.title

    @property
    def progress_percent(self) -> int:
        tasks = list(self.tasks.all())
        if not tasks:
            return 0
        return round(sum(1 for task in tasks if task.is_done) * 100 / len(tasks))


class PlanTask(models.Model):
    """مهمة في الخطة — وجسرها إلى التنفيذ.

    مثل ``Decision``: المهمة تتحوّل إلى تكليف ولا تُنسخ إليه، فمتابعتها هي
    متابعة تكليفها نفسه. والشرطان — مسؤول وموعد — إلزاميان للتحويل لا للتوثيق:
    خطةٌ تُكتب أهدافها قبل أن يُسمّى منفّذوها حالةٌ مشروعة.
    """

    plan = models.ForeignKey(
        Plan, on_delete=models.CASCADE, related_name="tasks", verbose_name="الخطة"
    )
    goal = models.ForeignKey(
        PlanGoal, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasks", verbose_name="الهدف",
    )
    order = models.PositiveSmallIntegerField("الترتيب", default=1)
    title = models.CharField("المهمة", max_length=255)
    description = models.TextField("التفصيل", blank=True, default="")

    responsible = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="plan_tasks", verbose_name="المسؤول",
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="plan_tasks", verbose_name="القسم المعني",
    )
    due_at = models.DateTimeField("موعد التنفيذ", null=True, blank=True)

    assignment = models.OneToOneField(
        "Assignment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="source_plan_task", verbose_name="التكليف المنبثق",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("order", "id")
        indexes = [models.Index(fields=["plan", "order"])]
        verbose_name = "مهمة خطة"
        verbose_name_plural = "مهام الخطط"

    def __str__(self) -> str:
        return self.title

    # ------------------------------------------------------------------
    @property
    def is_tracked(self) -> bool:
        return self.assignment_id is not None

    @property
    def is_done(self) -> bool:
        if not self.is_tracked:
            return False
        targets = list(self.assignment.targets.all())
        return bool(targets) and all(
            item.approval_state == ApprovalState.APPROVED for item in targets
        )

    @property
    def is_late(self) -> bool:
        if not self.is_tracked:
            return False
        return any(item.is_overdue for item in self.assignment.targets.all())

    @property
    def state(self) -> str:
        if not self.is_tracked:
            return "untracked"
        if self.is_done:
            return "done"
        if self.is_late:
            return "late"
        return "running"

    def can_become_assignment(self) -> bool:
        return (
            self.assignment_id is None
            and self.responsible_id is not None
            and self.due_at is not None
        )


class Initiative(ApprovalMixin):
    """مبادرة أو ممارسة ناجحة.

    **يقترحها أيٌّ كان، ويعتمدها المدير.** التوصيف يمنح المعلم «اقتراح مبادرة
    أو ممارسة ناجحة» ويمنح المدير «رفع المبادرات والممارسات الناجحة» — وهما
    طرفا دورة واحدة يخدمها ``ApprovalMixin`` بلا سطر جديد.

    **والمشاركة قرار تالٍ للاعتماد لا مرافق له.** مبادرةٌ تُشارَك مع المجموعة
    قبل أن تُعتمد تنقل إلى مدارس أخرى ما لم تتحقق مدرستها منه بعد. ولذلك
    ``shared_at`` منفصل عن ``decided_at``.
    """

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="initiatives",
        verbose_name="المدرسة",
    )
    teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name="initiatives",
        verbose_name="مقدّم المبادرة",
    )
    teacher_name = models.CharField(
        "اسم المقدّم (وقت الاقتراح)", max_length=150, blank=True, default=""
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="initiatives", verbose_name="الخطة المرتبطة",
    )

    title = models.CharField("عنوان المبادرة", max_length=200)
    summary = models.TextField("الفكرة والأثر", blank=True, default="")
    is_best_practice = models.BooleanField(
        "ممارسة ناجحة؟", default=False,
        help_text="تُقترح كممارسة تصلح لغير هذه المدرسة.",
    )
    shared_at = models.DateTimeField("شُوركت مع المجموعة في", null=True, blank=True)
    shared_by = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="initiatives_shared", verbose_name="شاركها",
    )

    created_at = models.DateTimeField("أُنشئت في", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["school", "-created_at"]),
            models.Index(fields=["school", "approval_state"]),
        ]
        verbose_name = "مبادرة"
        verbose_name_plural = "المبادرات"

    def __str__(self) -> str:
        return self.title

    @property
    def is_shared(self) -> bool:
        return self.shared_at is not None

    def can_share(self) -> bool:
        """لا تُشارَك إلا بعد اعتمادها — ومشاركةُ غير المعتمد نقلٌ لما لم يُتحقّق منه."""
        return self.approval_state == ApprovalState.APPROVED and not self.is_shared

    def allows_issuance(self, user, school) -> bool:
        """مدير المدرسة يُصدر مبادرته ولا يرفعها لمراجع لا وجود له.

        هذا إصدارٌ من صاحب السلطة، لا اعتمادٌ ذاتي. وبذلك يبقى سجل
        الانتقالات صحيحاً، ويبقى مقترح المعلم خاضعاً لمراجعة المدير.
        """
        from ..permissions import is_school_manager

        return (
            self.teacher_id == getattr(user, "pk", None)
            and self.school_id == getattr(school, "pk", None)
            and is_school_manager(user, active_school=school)
        )

    def assert_ready_for_submission(self) -> None:
        if not (self.summary or "").strip():
            raise ValidationError("اشرح فكرة المبادرة وأثرها قبل إرسالها.")

    def save(self, *args, **kwargs):
        if self.teacher_id and not self.teacher_name:
            try:
                self.teacher_name = (getattr(self.teacher, "name", "") or "")[:150]
            except Exception:
                pass
        return super().save(*args, **kwargs)
