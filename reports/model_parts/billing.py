from __future__ import annotations

from .base import *
from .schools import School

class SubscriptionPlan(models.Model):
    name = models.CharField("اسم الباقة", max_length=100)
    price = models.DecimalField("السعر", max_digits=10, decimal_places=2)
    days_duration = models.PositiveIntegerField(
        "المدة بالأيام", 
        help_text="مدة الباقة الافتراضية بالأيام (مثلاً 90 للفصل، 365 للسنة)"
    )
    description = models.TextField("المميزات", blank=True)
    is_active = models.BooleanField("نشطة", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    max_teachers = models.PositiveIntegerField(
        "حد المعلمين",
        default=0,
        help_text="الحد الأقصى لعدد حسابات المعلمين داخل المدرسة. 0 = غير محدود.",
    )

    class Meta:
        verbose_name = "باقة اشتراك"
        verbose_name_plural = "باقات الاشتراكات"

    def __str__(self):
        return f"{self.name} ({self.price} ريال)"


class SchoolSubscription(models.Model):
    school = models.OneToOneField(
        School,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="المدرسة"
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        verbose_name="الباقة الحالية"
    )
    start_date = models.DateField("تاريخ البدء")
    end_date = models.DateField("تاريخ الانتهاء", db_index=True)
    is_active = models.BooleanField(
        "نشط يدوياً", 
        default=True, 
        help_text="يمكن استخدامه لتعطيل الاشتراك مؤقتاً بغض النظر عن التاريخ"
    )

    canceled_at = models.DateTimeField(
        "تاريخ الإلغاء",
        null=True,
        blank=True,
        help_text="يُعبّأ عند إلغاء الاشتراك من مدير النظام.",
    )
    cancel_reason = models.TextField(
        "سبب الإلغاء",
        blank=True,
        help_text="يظهر للمدرسة عند إلغاء الاشتراك.",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "اشتراك مدرسة"
        verbose_name_plural = "اشتراكات المدارس"
        indexes = [
            models.Index(fields=['end_date', 'is_active']),
        ]

    def __str__(self):
        return f"اشتراك {self.school.name} - ينتهي في {self.end_date}"

    def save(self, *args, **kwargs):
        """ضبط تواريخ الاشتراك تلقائياً.

        المطلوب:
        - عند إنشاء اشتراك جديد: start_date = اليوم (ميلادي) و end_date حسب plan.days_duration.
        - عند تغيير الباقة (plan) في أي مكان (بما فيه Django admin): نعتبره تجديداً ونُعيد حساب التواريخ من اليوم.

        ملاحظة: لا نُعيد حساب التواريخ عند أي تعديل آخر (مثل تغيير is_active فقط)
        حتى لا يتم تمديد/تجديد الاشتراك بالخطأ.
        """
        from datetime import timedelta

        today = timezone.localdate()

        should_recalc = self.pk is None
        if not should_recalc and self.pk is not None:
            try:
                prev = SchoolSubscription.objects.filter(pk=self.pk).only("plan_id").first()
                if prev is not None and prev.plan_id != self.plan_id:
                    should_recalc = True
            except Exception:
                # في حال تعذّر مقارنة التغيير، لا نغيّر التواريخ على اشتراك موجود
                should_recalc = False

        if should_recalc:
            self.start_date = today
            days = int(getattr(self.plan, "days_duration", 0) or 0)
            if days <= 0:
                self.end_date = today
            else:
                # end_date = اليوم + (المدة - 1) حتى تكون الأيام الفعلية = days_duration
                self.end_date = today + timedelta(days=days - 1)

        return super().save(*args, **kwargs)

    @property
    def is_expired(self):
        if bool(self.is_cancelled):
            return True
        if not self.is_active:
            return True
        return timezone.now().date() > self.end_date

    @property
    def is_cancelled(self) -> bool:
        # الإلغاء المقصود: وجود تاريخ إلغاء (أو سبب) مع إيقاف الاشتراك.
        # لا نعتمد فقط على is_active=False لأن ذلك قد يُستخدم للإيقاف المؤقت.
        if bool(self.canceled_at) and not bool(self.is_active):
            return True
        if (self.cancel_reason or "").strip() and not bool(self.is_active):
            return True
        return False

    @property
    def days_remaining(self):
        delta = self.end_date - timezone.now().date()
        return delta.days


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "قيد المراجعة"
        APPROVED = "approved", "مقبول"
        REJECTED = "rejected", "مرفوض"
        CANCELLED = "cancelled", "ملغي"

    class Purpose(models.TextChoices):
        SUBSCRIPTION = "subscription", "اشتراك المدرسة"
        ARCHIVE_ADDON = "archive_addon", "إضافة الأرشفة"
        ARCHIVE_STORAGE = "archive_storage", "زيادة مساحة الأرشيف"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="المدرسة"
    )

    requested_plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_requests",
        verbose_name="الباقة المطلوبة",
    )

    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="payments",
        verbose_name="الاشتراك المرتبط"
    )
    amount = models.DecimalField("المبلغ", max_digits=10, decimal_places=2)
    purpose = models.CharField(
        "نوع العملية",
        max_length=32,
        choices=Purpose.choices,
        default=Purpose.SUBSCRIPTION,
        db_index=True,
    )
    archive_storage_gb = models.PositiveIntegerField(
        "مساحة أرشيف إضافية (GB)",
        default=0,
        help_text="تستخدم فقط عند طلب زيادة مساحة تخزين الأرشيف.",
    )
    batch_ref = models.CharField(
        "مرجع الطلب الموحّد",
        max_length=32,
        blank=True,
        default="",
        db_index=True,
        help_text="يربط عمليات الدفع التي أُنشئت معًا ضمن طلب موحّد واحد (إيصال واحد).",
    )
    receipt_image = models.ImageField(
        "صورة الإيصال",
        upload_to=_payment_receipt_upload_to,
        help_text="يرجى إرفاق صورة التحويل البنكي",
        null=True,
        blank=True,
        validators=[validate_image_file],
    )
    payment_date = models.DateField("تاريخ التحويل", default=timezone.now)
    status = models.CharField(
        "الحالة", 
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING, 
        db_index=True
    )
    notes = models.TextField("ملاحظات الإدارة", blank=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="قام بالرفع"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "عملية دفع"
        verbose_name_plural = "المدفوعات والإيرادات"
        ordering = ['-created_at']

    def __str__(self):
        return f"دفع #{self.id} - {self.school.name} - {self.amount}"


# =========================


class SchoolArchiveAddon(models.Model):
    """استحقاق مستقل لميزة أرشفة التقارير وملفات الإنجاز.

    هذا الملحق منفصل عن باقة الاشتراك الأساسية، بحيث يمكن تفعيله لأي مدرسة
    بغض النظر عن الخطة الحالية.
    """

    school = models.OneToOneField(
        School,
        on_delete=models.CASCADE,
        related_name="archive_addon",
        verbose_name="المدرسة",
    )
    is_enabled = models.BooleanField("مفعّل؟", default=True, db_index=True)
    start_date = models.DateField("تاريخ بداية الملحق", default=timezone.localdate)
    end_date = models.DateField(
        "تاريخ نهاية الملحق",
        null=True,
        blank=True,
        help_text="اتركه فارغًا إذا كان الملحق مفتوح المدة.",
        db_index=True,
    )
    storage_limit_gb = models.PositiveIntegerField("حد التخزين (GB)", default=50)
    storage_used_bytes = models.PositiveBigIntegerField("المستخدم من التخزين (بايت)", default=0)
    paid_amount = models.DecimalField("قيمة الملحق", max_digits=10, decimal_places=2, default=0)
    notes = models.TextField("ملاحظات", blank=True, default="")
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        verbose_name = "ملحق أرشفة مدرسة"
        verbose_name_plural = "ملحقات أرشفة المدارس"
        indexes = [
            models.Index(fields=["is_enabled", "end_date"]),
        ]

    def __str__(self):
        return f"أرشفة {self.school.name}"

    @property
    def is_active(self) -> bool:
        if not self.is_enabled:
            return False
        today = timezone.localdate()
        if self.start_date and self.start_date > today:
            return False
        if self.end_date and self.end_date < today:
            return False
        return True

    @property
    def days_remaining(self):
        if not self.end_date:
            return None
        return (self.end_date - timezone.localdate()).days

    @property
    def storage_used_gb(self) -> float:
        return round((self.storage_used_bytes or 0) / (1024 ** 3), 2)

    @property
    def storage_usage_percent(self) -> int:
        if not self.storage_limit_gb:
            return 0
        used_gb = (self.storage_used_bytes or 0) / (1024 ** 3)
        return min(100, int((used_gb / self.storage_limit_gb) * 100))


def school_has_archive_addon(school: School | None) -> bool:
    if school is None:
        return False
    try:
        addon = getattr(school, "archive_addon", None)
    except Exception:
        addon = None
    try:
        return bool(addon and addon.is_active)
    except Exception:
        return False


class ArchiveStorageOption(models.Model):
    """خيارات زيادة مساحة الأرشيف التي يديرها مدير النظام."""

    storage_gb = models.PositiveIntegerField("المساحة (GB)", validators=[MinValueValidator(1)])
    price = models.DecimalField("السعر", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    is_active = models.BooleanField("مفعّل؟", default=True, db_index=True)
    sort_order = models.PositiveSmallIntegerField("الترتيب", default=10)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        verbose_name = "خيار زيادة مساحة الأرشيف"
        verbose_name_plural = "خيارات زيادة مساحة الأرشيف"
        ordering = ["sort_order", "storage_gb", "id"]

    def __str__(self):
        return f"{self.storage_gb}GB - {self.price} ريال"
