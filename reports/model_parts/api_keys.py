from __future__ import annotations

import hashlib
import secrets

from .base import *  # noqa: F401,F403

# البادئة تُميّز مفاتيح المنصة في السجلات ولوحات الأسرار، وتجعل ماسحات
# التسريب (مثل ماسح GitHub) قادرة على التعرّف عليها بنمطٍ واحد.
API_KEY_PREFIX = "twq"
_PUBLIC_ID_BYTES = 6
_SECRET_BYTES = 32


def generate_api_key() -> tuple[str, str, str]:
    """يولّد مفتاحاً جديداً ويعيد ``(المفتاح الكامل، المعرِّف العلني، التجزئة)``.

    الصيغة ``twq_<معرِّف علني>_<سرّ>``. والقسمة إلى شقّين مقصودة: المعرِّف
    العلني يُخزَّن كما هو ليُعرَض في الشاشة ويُذكر في السجل ويُبحث به عند
    الإبطال؛ والسرّ لا يُخزَّن أبداً.
    """
    public_id = secrets.token_hex(_PUBLIC_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full = f"{API_KEY_PREFIX}_{public_id}_{secret}"
    return full, public_id, hash_api_key(full)


def hash_api_key(raw_key: str) -> str:
    """تجزئة المفتاح للتخزين والمقارنة.

    **لماذا SHA-256 لا bcrypt هنا؟** لأن هذا ليس كلمة مرور. كلمةُ المرور
    يختارها إنسان فتكون منخفضة الإنتروبيا وقابلة للتخمين، فتحتاج دالةً بطيئة
    عمداً. أما هذا فسرٌّ عشوائي بـ256 بتاً — لا يُخمَّن بأي قدرٍ من الحوسبة،
    والبطء فيه يكلّف كل نداء API ولا يشتري أماناً.

    والمقارنة تتم على التجزئة بفهرسٍ فريد، فلا حاجة لمرورٍ على كل الصفوف.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class SchoolApiKey(models.Model):  # noqa: F405
    """مفتاح تكامل لمدرسة واحدة.

    **لماذا لا يكفي الدخول بالجلسة.** الـAPI الحالي يقبل جلسةَ متصفّح وحدها،
    فلا يستطيع أي نظام خارجي — نظام الوزارة، أو جدول حصص، أو تقرير آلي —
    الاتصال به إلا بانتحال متصفّح. فالسطح موجود ولا يُستعمل.

    **والمفتاح مُقيَّدٌ بثلاثة حدود مجتمعة:**

    * **مدرسة واحدة.** ``school`` يُثبَّت على الطلب بوصفه المدرسة النشطة،
      فيمرّ المفتاح على طبقة العزل نفسها التي تحرس المتصفّح — لا مسار موازٍ
      له قواعده الخاصة. ومسارٌ موازٍ هو بالضبط ما ينسى أحدُهم تحديثه.

    * **هويةُ إنسان.** كل مفتاح مرتبط بـ``acting_as``: صلاحياته صلاحياتُ ذلك
      الشخص، لا أكثر. فلا يصير المفتاح بابَ تصعيدٍ للامتيازات، ويبقى ما يفعله
      منسوباً في سجلّ التدقيق إلى شخصٍ يُسأل — لا إلى «النظام».

    * **نطاق.** القراءة وحدها هي الافتراض. والكتابة تُمنح صراحةً، لأن أغلب
      التكاملات تقرأ فقط، ومفتاحٌ يكتب بلا داعٍ خسارةٌ محتملة بلا مقابل.

    **والسرّ يُعرض مرة واحدة.** لا يُخزَّن إلا مجزّأً، فلا يمكن استرجاعه —
    ومن فقده أنشأ غيره وأبطل القديم. وهذا هو الفرق بين تسريبٍ يُحتوى وتسريبٍ
    يُكتشف بعد شهور.
    """

    class Scope(models.TextChoices):  # noqa: F405
        READ = "read", "قراءة فقط"
        WRITE = "write", "قراءة وكتابة"

    school = models.ForeignKey(  # noqa: F405
        "reports.School",
        on_delete=models.CASCADE,
        related_name="api_keys",
        verbose_name="المدرسة",
    )
    name = models.CharField(
        "اسم التكامل",
        max_length=120,
        help_text="لأي نظام هذا المفتاح؟ يظهر في السجل عند كل استعمال.",
    )
    public_id = models.CharField(
        "المعرِّف العلني", max_length=32, unique=True, db_index=True, editable=False
    )
    key_hash = models.CharField(
        "تجزئة المفتاح", max_length=64, unique=True, db_index=True, editable=False
    )
    scope = models.CharField(
        "النطاق", max_length=10, choices=Scope.choices, default=Scope.READ
    )
    acting_as = models.ForeignKey(  # noqa: F405
        "reports.Teacher",
        on_delete=models.CASCADE,
        related_name="api_keys_acting",
        verbose_name="يعمل بصلاحيات",
        help_text="لا يملك المفتاح أكثر مما يملكه هذا الشخص في هذه المدرسة.",
    )
    created_by = models.ForeignKey(  # noqa: F405
        "reports.Teacher",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="api_keys_created",
        verbose_name="أنشأه",
    )
    is_active = models.BooleanField("نشط", default=True)
    expires_at = models.DateTimeField(
        "ينتهي في",
        null=True,
        blank=True,
        help_text="اتركه فارغاً لمفتاح دائم. المفاتيح المؤقّتة أأمن.",
    )
    last_used_at = models.DateTimeField("آخر استعمال", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", default=timezone.now)  # noqa: F405

    class Meta:
        verbose_name = "مفتاح تكامل"
        verbose_name_plural = "مفاتيح التكامل"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["school", "is_active"]),  # noqa: F405
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.public_id}"

    @property
    def can_write(self) -> bool:
        return self.scope == self.Scope.WRITE

    def is_usable(self, *, now=None) -> bool:
        """صالحٌ للاستعمال الآن؟

        يُفحص كذلك أن المدرسة والشخص المرتبط ما زالا نشطين: مفتاحٌ يبقى عاملاً
        بعد تعطيل صاحبه هو تصعيدُ امتيازٍ صامت — يخرج الموظف وتبقى صلاحياته.
        """
        moment = now or timezone.now()  # noqa: F405
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= moment:
            return False
        if not getattr(self.school, "is_active", False):
            return False
        return bool(getattr(self.acting_as, "is_active", False))
