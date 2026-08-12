# reports/totp.py
# -*- coding: utf-8 -*-
"""عامل ثانٍ بكلمة مرور لمرة واحدة (RFC 6238).

**لماذا إلى جانب Passkeys لا بدلاً منها.** Passkeys أقوى — لا سرّ مشترك ولا
شيء يُصطاد بالتصيّد. لكنها تحتاج جهازاً ومتصفّحاً يدعمان WebAuthn، وكثيرٌ من
أجهزة المدارس الحكومية لا يدعمانها. فالبديل الواقعي لمن لا يملك ذلك ليس عاملاً
أضعف، بل **لا عامل ثانٍ إطلاقاً**. وTOTP يعمل على أي هاتف.

**ولماذا نُفِّذ هنا لا بمكتبة.** ``pyotp`` غير مثبَّتة، وإضافتها تعني إعادة
توليد قفل الاعتماديات وتمريرة ``pip-audit`` — قرارُ سلسلة إمداد. والخوارزمية
موصوفة في RFC 6238 بدقة وتُنفَّذ في ثلاثين سطراً على المكتبة القياسية، وكلها
بدائيات مُدقَّقة (``hmac`` و``hashlib``). فالكتابة هنا أقلّ مخاطرةً من اعتمادية
جديدة، لا أكثر.

**والسرّ مُعمّى في القاعدة.** سرُّ TOTP سرٌّ مشترك: من يملكه يولّد رموزاً
صحيحة إلى الأبد. وتخزينه نصاً يعني أن تسريب نسخة من قاعدة البيانات يُبطل
العامل الثاني لكل المستخدمين دفعةً واحدة — أي أن الحماية تسقط في اللحظة التي
وُجدت لها. فيُعمّى بـFernet بمفتاح مشتقّ.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

# معايير RFC 6238 المتعارف عليها، وهي ما تفترضه تطبيقات المصادقة كلها.
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
# نافذة تسامح خطوةً واحدة في كل اتجاه: ساعةُ الهاتف تنحرف، ورفضُ رمزٍ صحيح
# بسبب ثانيتين يدفع المستخدم إلى تعطيل العامل الثاني كلّه.
TOTP_DRIFT_STEPS = 1

SECRET_BYTES = 20  # 160 بتاً — ما توصي به RFC 4226
RECOVERY_CODE_COUNT = 10


# ── تعمية السرّ ──────────────────────────────────────────────────────────
def _encryption_key() -> bytes:
    """مفتاح تعمية أسرار TOTP.

    يُقرأ من ``TOTP_SECRET_ENCRYPTION_KEY`` إن ضُبط، وإلا يُشتقّ من
    ``SECRET_KEY`` عبر HKDF.

    **والاشتقاق له ثمنٌ يجب أن يُعرف:** تدوير ``SECRET_KEY`` يُبطل كل أسرار
    TOTP المخزَّنة، فيُضطرّ كل مستخدم إلى إعادة التسجيل. ولذلك يوجد المتغيّر
    المستقل: من يريد تدوير مفتاح التوقيع دون لمس العامل الثاني يضبطه مرة واحدة.
    """
    explicit = (getattr(settings, "TOTP_SECRET_ENCRYPTION_KEY", "") or "").strip()
    if explicit:
        material = explicit.encode("utf-8")
    else:
        material = str(settings.SECRET_KEY).encode("utf-8")

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"tawtheeq-totp-v1",
        info=b"totp-secret-encryption",
    ).derive(material)
    return base64.urlsafe_b64encode(derived)


def encrypt_secret(secret_b32: str) -> str:
    return Fernet(_encryption_key()).encrypt(secret_b32.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """يفكّ التعمية، أو ``None`` إن تعذّر.

    التعذّر يعني عملياً أن مفتاح التعمية تغيّر (تدوير ``SECRET_KEY`` غالباً).
    والصواب حينها رفضُ الرمز لا قبولُه: عاملٌ ثانٍ لا يمكن التحقق منه ليس
    عاملاً ثانياً.
    """
    try:
        return Fernet(_encryption_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


# ── الخوارزمية ───────────────────────────────────────────────────────────
def generate_secret() -> str:
    """سرٌّ جديد بصيغة Base32 كما تتوقّعه تطبيقات المصادقة."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _counter_for(moment: float | None = None) -> int:
    return int((moment if moment is not None else time.time()) // TOTP_PERIOD_SECONDS)


def code_for_counter(secret_b32: str, counter: int) -> str:
    """HOTP لعدّادٍ محدَّد — قلبُ RFC 4226/6238."""
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32 + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_code(
    secret_b32: str,
    code: str,
    *,
    last_used_counter: int | None = None,
    moment: float | None = None,
) -> int | None:
    """يتحقق من الرمز ويعيد عدّاده، أو ``None`` إن فشل.

    **إعادةُ العدّاد ليست تفصيلاً:** المتصل يخزّنها ويرفض أي رمزٍ عدّاده أقلّ
    منها أو يساويها. وبدون ذلك يبقى الرمز صالحاً ثلاثين ثانية بعد استعماله —
    فمن التقطه من فوق الكتف أو من شبكةٍ وسيطة يستطيع إعادة استعماله. وهذا هو
    فرق «كلمة مرور لمرة واحدة» عن «كلمة مرور قصيرة العمر».

    والمقارنة بـ``compare_digest`` لأن المقارنة العادية تنتهي عند أول محرف
    مختلف، فيتسرّب من زمنها كم محرفاً صحّ.
    """
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != TOTP_DIGITS:
        return None

    current = _counter_for(moment)
    for drift in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        counter = current + drift
        if counter < 0:
            continue
        if last_used_counter is not None and counter <= last_used_counter:
            # رمزٌ استُعمل من قبل — لا يُقبل مرتين.
            continue
        if hmac.compare_digest(code_for_counter(secret_b32, counter), code):
            return counter
    return None


def provisioning_uri(secret_b32: str, *, account: str, issuer: str) -> str:
    """رابط ``otpauth://`` الذي تقرؤه تطبيقات المصادقة.

    على الهاتف يفتح التطبيق مباشرةً عند النقر — وهو المسار الذي يسلكه أغلب
    المستخدمين فعلاً، إذ يُسجَّل العامل الثاني من الهاتف لا من الحاسوب.
    """
    label = quote(f"{issuer}:{account}", safe="")
    params = (
        f"secret={secret_b32}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )
    return f"otpauth://totp/{label}?{params}"


# ── رموز الاسترجاع ───────────────────────────────────────────────────────
def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """رموز استرجاع تُستعمل مرة واحدة.

    **بدونها يصير فقدُ الهاتف فقداً للحساب.** ومنصةٌ تُقفل معلّماً خارج عمله
    لأنه غيّر جهازه ستدفعه — ومن حوله — إلى تعطيل العامل الثاني كلّه. فالحماية
    التي لا مخرج منها حمايةٌ تُلغى.
    """
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """تجزئة رمز الاسترجاع.

    SHA-256 لا bcrypt: الرمز عشوائيٌّ بـ48 بتاً لا يختاره إنسان، فلا يُخمَّن —
    وهو المنطق نفسه المطبَّق على مفاتيح التكامل.
    """
    return hashlib.sha256(normalise_recovery_code(code).encode("utf-8")).hexdigest()


def normalise_recovery_code(code: str) -> str:
    """يقبل الرمز بشرطات أو بدونها، وبأي حالة أحرف."""
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())
