from __future__ import annotations

from .base import *
from .schools import Department, School, SchoolMembership, Teacher

__all__ = ["StaffScope", "Delegation"]


class StaffScope(models.Model):
    """نطاق صلاحية منسوب — ما الذي يملكه، وعلى مَن.

    **مربوط بالعضوية لا بالحساب.** الرابط ``OneToOne`` إلى ``SchoolMembership``
    لا إلى ``Teacher``، فالنطاق يموت بموت العضوية ولا يتبع صاحبه إلى مدرسة
    أخرى. هذا هو الفرق الذي أسقط الأدوار الوسيطة السابقة في هذا المشروع حين
    كانت أعلاماً على الحساب.

    **يخدم الوكيل والموظف الإداري معاً.** التوصيف يمنح الموظف الإداري «إعداد
    مسودات التعاميم *عند منحه الصلاحية*» — وهي الحاجة نفسها التي يخدمها نطاق
    الوكيل. فنموذجان متوازيان لمفهوم واحد يعنيان شاشتين وقاعدتين تتباعدان.

    **الصلاحيات قائمة رموز لا أعمدة منطقية.** عمود لكل صلاحية يعني ترحيلاً عند
    كل إضافة، وجدولاً يتضخم بأعمدة معظمها ``False``. والرموز مُتحقَّق منها عند
    الحفظ مقابل مرجع الكود، فرمزٌ لا يقرؤه أحد لا يُخزَّن أصلاً.
    """

    class Domain(models.TextChoices):
        """مجال الوكالة — تنظيمي بحت، لا يمنح صلاحية بذاته."""

        ACADEMIC = "academic", "الشؤون التعليمية"
        OPERATIONS = "operations", "الشؤون المدرسية"
        STUDENTS = "students", "شؤون الطلاب"
        SHARED = "shared", "صلاحيات مشتركة"

    membership = models.OneToOneField(
        SchoolMembership,
        on_delete=models.CASCADE,
        related_name="scope",
        verbose_name="العضوية",
    )
    domain = models.CharField(
        "مجال الوكالة",
        max_length=16,
        choices=Domain.choices,
        blank=True,
        default="",
        help_text="خاص بالوكيل. يوضّح مجال إشرافه ولا يمنح صلاحية بذاته.",
    )
    departments = models.ManyToManyField(
        Department,
        blank=True,
        related_name="supervising_scopes",
        verbose_name="الأقسام المشمولة",
        help_text="الأقسام التي يشرف عليها. تركها فارغة يعني نطاقاً بلا أقسام مُسندة.",
    )
    capabilities = models.JSONField(
        "الصلاحيات الممنوحة",
        default=list,
        blank=True,
        help_text="رموز من مرجع الصلاحيات في الكود. أي رمز مجهول يُسقَط عند الحفظ.",
    )
    template_code = models.CharField(
        "القالب المُطبَّق",
        max_length=64,
        blank=True,
        default="",
        help_text="القالب الذي انطلق منه المدير. يُفرَّغ عند أي تعديل يدوي بعده.",
    )
    granted_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_staff_scopes",
        verbose_name="مَن منح النطاق",
    )
    created_at = models.DateTimeField("أُنشئ في", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)

    class Meta:
        verbose_name = "نطاق صلاحية"
        verbose_name_plural = "نطاقات الصلاحيات"

    def __str__(self) -> str:
        return f"نطاق {self.membership}"

    # ------------------------------------------------------------------
    @property
    def school(self) -> School | None:
        return getattr(self.membership, "school", None)

    @property
    def role_type(self) -> str:
        return str(getattr(self.membership, "role_type", "") or "")

    def capability_codes(self) -> set[str]:
        """الصلاحيات الفعّالة — المتاحة منها فقط.

        الصلاحية المعلَّقة (ميزتها لم تُبنَ) تبقى **مخزَّنة** ليحتفظ اختيار
        المدير بأثره، ولا تُعاد هنا لئلا يظن فاحصٌ أنها نافذة. فمنحُ صلاحية
        جوفاء أسوأ من عدم عرضها.
        """
        from ..capabilities import AVAILABLE_CODES

        stored = {str(code) for code in (self.capabilities or [])}
        return stored & set(AVAILABLE_CODES)

    def clean(self):
        super().clean()
        from ..capabilities import sanitize

        role = self.role_type
        if role and role not in SchoolMembership.STAFF_ROLES:
            raise ValidationError("النطاق يُمنح لمنسوبي المدرسة فقط، لا لمديرها.")
        if self.domain and role != SchoolMembership.RoleType.DEPUTY:
            raise ValidationError({"domain": "مجال الوكالة خاص بالوكيل وحده."})

        self.capabilities = sanitize(self.capabilities, role=role or None)

    def save(self, *args, **kwargs):
        from ..capabilities import sanitize

        # التنقية في ``save`` أيضاً لا في ``clean`` وحدها: النطاقات تُنشأ من
        # الكود كما تُنشأ من النماذج، ومسارٌ يتخطى التحقق يجب ألا يخزّن رمزاً
        # لا يقرؤه أحد.
        self.capabilities = sanitize(self.capabilities, role=self.role_type or None)
        return super().save(*args, **kwargs)


class Delegation(models.Model):
    """تفويض مؤقت من مدير المدرسة.

    **ينتهي بذاته.** الصلاحية محسوبة من ``starts_at``/``ends_at`` عند كل طلب،
    لا مضبوطة بمهمة مجدولة تُلغيها. تفويضٌ ينتهي مفعوله لأن وظيفةً دورية عملت
    ليس تفويضاً مؤقتاً — هو تفويض دائم يُرجى أن يُسحب.

    **يُمارَس بالنيابة لا بالأصالة.** كل إجراء يُنفَّذ باستعمال هذا التفويض
    يُسجَّل في سجل الإجراءات موسوماً بأنه نيابة عن المدير. وهذا هو الفرق العملي
    بين تفويض ومنحة دائمة: المسؤولية تبقى مقروءة بعد انقضاء المدة.
    """

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="delegations",
        verbose_name="المدرسة",
    )
    delegator = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="delegations_granted",
        verbose_name="المفوِّض",
    )
    delegate = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="delegations_received",
        verbose_name="المفوَّض إليه",
    )
    capabilities = models.JSONField(
        "الصلاحيات المفوَّضة",
        default=list,
        blank=True,
    )
    reason = models.CharField(
        "سبب التفويض",
        max_length=255,
        blank=True,
        default="",
        help_text="يظهر في سجل الإجراءات وفي شاشة المدير.",
    )
    starts_at = models.DateTimeField("يبدأ في", default=timezone.now)
    ends_at = models.DateTimeField(
        "ينتهي في",
        help_text="التفويض بلا نهاية ليس تفويضاً — فالمدة إلزامية.",
    )
    revoked_at = models.DateTimeField("سُحب في", null=True, blank=True)
    revoked_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delegations_revoked",
        verbose_name="مَن سحبه",
    )
    created_at = models.DateTimeField("أُنشئ في", auto_now_add=True)

    class Meta:
        ordering = ("-starts_at", "-id")
        indexes = [
            models.Index(fields=["school", "delegate", "-starts_at"]),
            models.Index(fields=["school", "-starts_at"]),
        ]
        verbose_name = "تفويض"
        verbose_name_plural = "التفويضات"

    def __str__(self) -> str:
        return f"تفويض {self.delegate} @ {self.school}"

    # ------------------------------------------------------------------
    def is_active_at(self, moment=None) -> bool:
        moment = moment or timezone.now()
        if self.revoked_at is not None and self.revoked_at <= moment:
            return False
        return self.starts_at <= moment <= self.ends_at

    @property
    def is_active(self) -> bool:
        return self.is_active_at()

    @property
    def state(self) -> str:
        """حالة التفويض للعرض: مسحوب / منتظر / سارٍ / منتهٍ."""
        now = timezone.now()
        if self.revoked_at is not None and self.revoked_at <= now:
            return "revoked"
        if now < self.starts_at:
            return "scheduled"
        if now > self.ends_at:
            return "expired"
        return "active"

    def capability_codes(self) -> set[str]:
        from ..capabilities import AVAILABLE_CODES

        return {str(code) for code in (self.capabilities or [])} & set(AVAILABLE_CODES)

    def clean(self):
        super().clean()
        from ..capabilities import sanitize

        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "نهاية التفويض يجب أن تكون بعد بدايته."})
        if self.delegator_id and self.delegate_id and self.delegator_id == self.delegate_id:
            raise ValidationError({"delegate": "لا يفوّض المدير نفسه."})
        # التفويض يُمنح لمنسوب في المدرسة نفسها. تفويض من هو خارجها يفتح
        # المدرسة على من لا عضوية له فيها.
        if self.school_id and self.delegate_id:
            is_member = SchoolMembership.objects.filter(
                school_id=self.school_id,
                teacher_id=self.delegate_id,
                is_active=True,
                role_type__in=SchoolMembership.STAFF_ROLES,
            ).exists()
            if not is_member:
                raise ValidationError({"delegate": "التفويض يُمنح لمنسوبي هذه المدرسة فقط."})

        self.capabilities = sanitize(self.capabilities)

    def save(self, *args, **kwargs):
        from ..capabilities import sanitize

        self.capabilities = sanitize(self.capabilities)
        return super().save(*args, **kwargs)

    def revoke(self, *, by=None) -> None:
        """سحب التفويض. لا يُحذف الصف — فالمسحوب واقعة يجب أن تبقى مقروءة."""
        if self.revoked_at is not None:
            return
        self.revoked_at = timezone.now()
        self.revoked_by = by
        self.save(update_fields=["revoked_at", "revoked_by"])
