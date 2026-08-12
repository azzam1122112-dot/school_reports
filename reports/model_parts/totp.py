from __future__ import annotations

from .base import *  # noqa: F401,F403


class TeacherTotpDevice(models.Model):  # noqa: F405
    """جهاز مصادقة (TOTP) واحد لكل مستخدم.

    **واحدٌ لا عدة أجهزة عمداً.** تعدُّد الأجهزة يضاعف سطح السرّ المشترك بلا
    أن يحلّ المشكلة التي وُجد لها: من فقد هاتفه يستعمل رمز استرجاع، لا هاتفاً
    ثانياً سجّله احتياطاً ونسي وجوده.

    ``confirmed_at`` هو الفارق بين تسجيلٍ بدأ وتسجيلٍ اكتمل: السرّ يُولَّد أولاً
    ويُعرض، ولا يصير الجهاز نافذاً إلا بعد أن يُثبت المستخدم أنه أدخله بنجاح.
    وبدون هذه الخطوة يُقفل من أخطأ في المسح خارج حسابه فوراً.
    """

    teacher = models.OneToOneField(  # noqa: F405
        "reports.Teacher",
        on_delete=models.CASCADE,
        related_name="totp_device",
        verbose_name="المستخدم",
    )
    secret_encrypted = models.TextField(
        "السرّ (مُعمّى)",
        editable=False,
        help_text="لا يُخزَّن نصاً: تسريب نسخة من القاعدة كان سيُبطل العامل الثاني للجميع.",
    )
    confirmed_at = models.DateTimeField(
        "تاريخ التفعيل",
        null=True,
        blank=True,
        help_text="فارغ = تسجيلٌ بدأ ولم يكتمل. الجهاز غير النافذ لا يُطالَب به عند الدخول.",
    )
    last_used_counter = models.BigIntegerField(
        "آخر عدّاد مستعمَل",
        null=True,
        blank=True,
        editable=False,
        help_text="يمنع إعادة استعمال الرمز نفسه داخل نافذته.",
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)  # noqa: F405
    last_used_at = models.DateTimeField("آخر استعمال", null=True, blank=True)

    class Meta:
        verbose_name = "جهاز مصادقة ثنائية"
        verbose_name_plural = "أجهزة المصادقة الثنائية"

    def __str__(self) -> str:
        state = "نافذ" if self.is_confirmed else "غير مكتمل"
        return f"TOTP · {self.teacher_id} · {state}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class TotpRecoveryCode(models.Model):  # noqa: F405
    """رمز استرجاع يُستعمل مرة واحدة.

    يُخزَّن مجزّأً لا نصاً: هو بديلٌ كامل عن العامل الثاني، فتسريبه بتسريب
    القاعدة يُبطله. ولا يُحذف بعد الاستعمال بل يُختم بوقته — فيبقى في السجل
    أثرُ أن أحدهم دخل برمز استرجاع، وهو حدثٌ يستحق أن يُرى.
    """

    device = models.ForeignKey(  # noqa: F405
        TeacherTotpDevice,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
        verbose_name="الجهاز",
    )
    code_hash = models.CharField("تجزئة الرمز", max_length=64, db_index=True, editable=False)
    used_at = models.DateTimeField("تاريخ الاستعمال", null=True, blank=True)

    class Meta:
        verbose_name = "رمز استرجاع"
        verbose_name_plural = "رموز الاسترجاع"
        constraints = [
            models.UniqueConstraint(  # noqa: F405
                fields=["device", "code_hash"], name="unique_recovery_code_per_device"
            ),
        ]

    def __str__(self) -> str:
        return f"RecoveryCode#{self.pk} · {'مستعمَل' if self.used_at else 'متاح'}"
