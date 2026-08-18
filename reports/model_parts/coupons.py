# reports/model_parts/coupons.py
# -*- coding: utf-8 -*-
"""أكواد الخصم التي تصدرها إدارة المنصة لاشتراكات المدارس.

قاعدتان تحكمان الكود:
- عدد استخدامات كلي (``max_uses``) يُستهلك على مستوى المنصة كلها.
- استخدام واحد لكل مدرسة، ويضمنه قيد فريد في ``DiscountRedemption`` على
  مستوى قاعدة البيانات — لا على مستوى الكود — فلا يخترقه تكرار الإرسال.

لا يوجد عدّاد مخزّن للاستخدامات؛ المصدر الوحيد هو عدّ سجلات ``DiscountRedemption``
تحت قفل صف الكود وقت الحجز، فلا ينحرف العدّاد عن الواقع عند التحرير أو الرفض.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .base import *
from .schools import School


class DiscountCode(models.Model):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "نسبة مئوية"
        FIXED = "fixed", "مبلغ ثابت"

    code = models.CharField(
        "الكود",
        max_length=32,
        unique=True,
        help_text="أحرف إنجليزية كبيرة وأرقام (والشرطة -). يُوحَّد تلقائياً إلى أحرف كبيرة.",
    )
    discount_type = models.CharField(
        "نوع الخصم",
        max_length=16,
        choices=DiscountType.choices,
        default=DiscountType.PERCENT,
    )
    value = models.DecimalField(
        "قيمة الخصم",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="نسبة مئوية (حتى 100) أو مبلغ بالريال حسب نوع الخصم.",
    )
    max_uses = models.PositiveIntegerField(
        "عدد الاستخدامات الكلي",
        validators=[MinValueValidator(1)],
        help_text="إجمالي مرات الاستخدام المتاحة لجميع المدارس. كل مدرسة تستخدم الكود مرة واحدة فقط.",
    )
    valid_from = models.DateField(
        "يسري من",
        null=True,
        blank=True,
        help_text="اتركه فارغاً ليسري فوراً.",
    )
    valid_until = models.DateField(
        "يسري حتى",
        null=True,
        blank=True,
        help_text="آخر يوم يُقبل فيه الكود. اتركه فارغاً بلا تاريخ انتهاء.",
    )
    is_active = models.BooleanField("نشط", default=True, db_index=True)
    notes = models.TextField("ملاحظات داخلية", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_discount_codes",
        verbose_name="أصدره",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        verbose_name = "كود خصم"
        verbose_name_plural = "أكواد الخصم"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()}: {self.value})"

    def clean(self):
        super().clean()
        if (
            self.discount_type == self.DiscountType.PERCENT
            and self.value is not None
            and self.value > Decimal("100")
        ):
            raise ValidationError({"value": "النسبة المئوية لا تتجاوز 100."})
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValidationError({"valid_until": "تاريخ الانتهاء يسبق تاريخ البداية."})

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        return super().save(*args, **kwargs)

    # ── حالة الكود ──
    @property
    def used_count(self) -> int:
        return self.redemptions.count()

    @property
    def remaining_uses(self) -> int:
        return max(0, int(self.max_uses or 0) - self.used_count)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_uses <= 0

    @property
    def is_expired(self) -> bool:
        return bool(self.valid_until and timezone.localdate() > self.valid_until)

    @property
    def is_scheduled(self) -> bool:
        return bool(self.valid_from and timezone.localdate() < self.valid_from)

    @property
    def is_usable_now(self) -> bool:
        return bool(
            self.is_active
            and not self.is_expired
            and not self.is_scheduled
            and not self.is_exhausted
        )

    def discount_for(self, amount: Decimal) -> Decimal:
        """قيمة الخصم لمبلغ معيّن: لا تتجاوز المبلغ ولا تنزل عن الصفر."""
        amount = Decimal(str(amount))
        if self.discount_type == self.DiscountType.PERCENT:
            discount = (amount * Decimal(self.value) / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            discount = Decimal(self.value).quantize(Decimal("0.01"))
        if discount < Decimal("0.00"):
            return Decimal("0.00")
        return min(discount, amount)

    @property
    def display_value(self) -> str:
        if self.discount_type == self.DiscountType.PERCENT:
            return f"{Decimal(self.value).normalize():f}%"
        return f"{self.value} ريال"


class DiscountRedemption(models.Model):
    """استخدام مدرسةٍ لكود خصم — يُحجز عند إنشاء طلب الدفع.

    الحجز عند الإنشاء لا عند الاعتماد: كودٌ بعشرة استخدامات لا يجوز أن يدخل به
    ثلاثون طلباً «قيد المراجعة» معاً. وإذا رُفض الطلب أو أُلغي قبل تطبيق أثره
    يُحذف هذا السجل فيتحرر الاستخدام (انظر ``release_dead_redemptions``).
    """

    code = models.ForeignKey(
        DiscountCode,
        on_delete=models.CASCADE,
        related_name="redemptions",
        verbose_name="كود الخصم",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="discount_redemptions",
        verbose_name="المدرسة",
    )
    payment = models.ForeignKey(
        "Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discount_redemptions",
        verbose_name="عملية الدفع",
    )
    batch_ref = models.CharField("مرجع الطلب الموحّد", max_length=32, blank=True, default="")
    amount_discounted = models.DecimalField(
        "قيمة الخصم الممنوحة",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField("تاريخ الاستخدام", auto_now_add=True)

    class Meta:
        verbose_name = "استخدام كود خصم"
        verbose_name_plural = "استخدامات أكواد الخصم"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "school"],
                name="uniq_discount_code_per_school",
            ),
        ]

    def __str__(self):
        return f"{self.code.code} → {self.school.name}"
