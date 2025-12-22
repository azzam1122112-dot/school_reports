# reports/validators.py
# -*- coding: utf-8 -*-

from django.core.exceptions import ValidationError

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    Image = None
    UnidentifiedImageError = Exception


MAX_IMAGE_MB = 10
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024
ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def validate_image_size(file_obj):
    """Reject images larger than MAX_IMAGE_MB."""
    if getattr(file_obj, "size", 0) > MAX_IMAGE_BYTES:
        raise ValidationError(f"حجم الصورة يتجاوز {MAX_IMAGE_MB}MB.")


def validate_image_file(file_obj):
    """Server-side validation: images only + size limit.

    - Checks extension & Content-Type when available.
    - Verifies image can be opened via Pillow (when installed).
    """
    validate_image_size(file_obj)

    name = (getattr(file_obj, "name", "") or "").lower()
    if name and not name.endswith(ALLOWED_IMAGE_EXTS):
        raise ValidationError("يُسمح برفع الصور فقط (JPG/PNG/WEBP).")

    content_type = (getattr(file_obj, "content_type", "") or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValidationError("يُسمح برفع الصور فقط.")

    if Image is None:
        return

    try:
        try:
            file_obj.seek(0)
        except Exception:
            pass

        img = Image.open(file_obj)
        img.verify()

    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("الملف المرفوع ليس صورة صالحة.")
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass
