# reports/storage.py
# -*- coding: utf-8 -*-
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.exceptions import ImproperlyConfigured

from io import BytesIO

from django.core.files.base import ContentFile

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # Pillow غير متوفر؟ نتخطى الضغط بهدوء
    Image = None
    UnidentifiedImageError = Exception

# نحاول استيراد Cloudinary، لكن نستخدمه فقط إذا كان مفعلًا في الإعدادات
try:
    from cloudinary_storage.storage import RawMediaCloudinaryStorage, MediaCloudinaryStorage
except (ImportError, ImproperlyConfigured):
    RawMediaCloudinaryStorage = None
    MediaCloudinaryStorage = None


def _compress_image_file(file_obj, max_size: int = 1600, jpeg_quality: int = 90):
    """يضغط الصورة (إن كانت صورة) قبل رفعها إلى Cloudinary.

    - يقلّل الأبعاد بحيث لا يتجاوز أي بُعد max_size (مثلاً 1600px)
    - يحفظ JPEG بجودة عالية (افتراضيًا 90) مع optimize=True
    - لا يلمس الملفات غير الصورية أو GIF/WEBP (لتجنّب كسر الصور المتحركة)
    """
    if Image is None:
        # في حال لم يكن Pillow مثبتًا لأي سبب
        return file_obj

    # نضمن أن مؤشر الملف في البداية
    try:
        file_obj.seek(0)
    except Exception:
        pass

    try:
        img = Image.open(file_obj)
    except (UnidentifiedImageError, OSError, ValueError):
        # ليس ملف صورة معروف → نعيده كما هو
        try:
            file_obj.seek(0)
        except Exception:
            pass
        return file_obj

    img_format = (img.format or "JPEG").upper()

    # نتجنّب العبث بـ GIF/WEBP لاحتمال أن تكون متحركة
    if img_format in {"GIF", "WEBP"}:
        try:
            file_obj.seek(0)
        except Exception:
            pass
        return file_obj

    # تصغير الأبعاد إذا كانت كبيرة جدًا
    try:
        width, height = img.size
        if max(width, height) > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
    except Exception:
        # في حال فشل أي شيء، نُكمل بدون تغيير الأبعاد
        pass

    # معالجة الأنماط اللونية قبل الحفظ كـ JPEG
    if img_format in {"JPEG", "JPG"} and img.mode in {"RGBA", "P"}:
        img = img.convert("RGB")

    buffer = BytesIO()

    # إعدادات الحفظ حسب النوع
    save_kwargs = {}
    if img_format in {"JPEG", "JPG"}:
        save_kwargs["quality"] = jpeg_quality
        save_kwargs["optimize"] = True
        save_format = "JPEG"
    elif img_format == "PNG":
        save_kwargs["optimize"] = True
        save_format = "PNG"
    else:
        save_format = img_format

    try:
        img.save(buffer, format=save_format, **save_kwargs)
    except OSError:
        # في بعض الحالات لا يدعم optimizer → نحفظ بدون خيارات خاصة
        buffer = BytesIO()
        img.save(buffer, format=save_format)

    buffer.seek(0)
    compressed = ContentFile(buffer.read())
    # الاحتفاظ بنفس الاسم ليسهّل التوافق
    compressed.name = getattr(file_obj, "name", None) or "image"
    return compressed


# نتحقق هل Cloudinary مفعل في settings.py؟
_use_cloudinary = (
    MediaCloudinaryStorage is not None
    and RawMediaCloudinaryStorage is not None
    and hasattr(settings, "CLOUDINARY_STORAGE")
    and settings.CLOUDINARY_STORAGE
)


if _use_cloudinary:
    class CompressedMediaCloudinaryStorage(MediaCloudinaryStorage):
        """تخزين Cloudinary مع ضغط تلقائي للصور قبل الرفع.

        يُستخدم هذا التخزين كـ DEFAULT_FILE_STORAGE بحيث يشمل:
        - كل ImageField
        - كل FileField الذي يرفع صورة (سيفحص الامتداد/المحتوى)
        """

        def _save(self, name, content):  # type: ignore[override]
            content = _compress_image_file(content)
            return super()._save(name, content)


    class PublicRawMediaStorage(RawMediaCloudinaryStorage):
        """تخزين Cloudinary للملفات العامة كـ RAW (PDF/DOCX/ZIP/صور).

        - يرفع الملفات تحت resource_type="raw"
        - الوصول عام (type=upload)
        - إذا كان الملف صورة (jpg/png/webp ...) يتم ضغطها قبل الرفع
        """

        def _save(self, name, content):  # type: ignore[override]
            content = _compress_image_file(content)
            return super()._save(name, content)
else:
    # في بيئة التطوير المحلية (بدون Cloudinary) نستخدم التخزين المحلي العادي
    class PublicRawMediaStorage(FileSystemStorage):
        pass

    # نعرّف CompressedMediaCloudinaryStorage كتخزين محلي بسيط للاتساق،
    # لكن بدون أي منطق خاص (لا يوجد Cloudinary هنا).
    class CompressedMediaCloudinaryStorage(FileSystemStorage):
        pass

