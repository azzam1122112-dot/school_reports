"""Upload validators.

Step 3 scope (security):
- Extension allowlists
- Size limits
- Block dangerous types (svg/html/js)
- MIME detection via python-magic when available, with safe fallbacks on Windows/dev
"""

from __future__ import annotations

import mimetypes
import os
from typing import Iterable

from django.core.exceptions import ValidationError

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:
    Image = None
    UnidentifiedImageError = Exception

try:
    import magic  # type: ignore
except Exception:
    magic = None


MAX_IMAGE_MB = 10
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024

MAX_ATTACHMENT_MB = 5
MAX_ATTACHMENT_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024

ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
ALLOWED_ATTACHMENT_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx")
ALLOWED_CIRCULAR_ATTACHMENT_EXTS = (".pdf", ".jpg", ".jpeg", ".png")

BLOCKED_EXTS = (".svg", ".html", ".htm", ".js")
BLOCKED_MIME_PREFIXES = (
    "text/html",
    "application/javascript",
    "text/javascript",
    "image/svg",
    "image/svg+xml",
)


def _get_ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def _read_head(file_obj, n: int = 4096) -> bytes:
    try:
        file_obj.seek(0)
    except Exception:
        pass
    try:
        head = file_obj.read(n)
    except Exception:
        head = b""
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass
    return head or b""


def _sniff_mime(file_obj, name: str) -> str:
    """Best-effort MIME detection.

    Prefers python-magic when available; otherwise uses mimetypes + simple signature checks.
    """
    if magic is not None:
        try:
            head = _read_head(file_obj, 8192)
            return (magic.from_buffer(head, mime=True) or "").lower()
        except Exception:
            pass

    guessed, _ = mimetypes.guess_type(name or "")
    guessed = (guessed or "").lower()

    head = _read_head(file_obj, 32)
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and b"WEBP" in head[:16]:
        return "image/webp"
    if head.startswith(b"PK\x03\x04") and _get_ext(name) == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and _get_ext(name) == ".doc":
        return "application/msword"

    return guessed


def _validate_size(file_obj, *, max_bytes: int, label_ar: str) -> None:
    if getattr(file_obj, "size", 0) > max_bytes:
        raise ValidationError(f"حجم {label_ar} يتجاوز {max_bytes // (1024 * 1024)}MB.")


def _validate_ext(name: str, *, allowed_exts: Iterable[str], label_ar: str) -> None:
    ext = _get_ext(name)
    if ext in BLOCKED_EXTS:
        raise ValidationError("امتداد الملف غير مسموح.")
    if ext and ext not in tuple(allowed_exts):
        raise ValidationError(f"نوع {label_ar} غير مسموح.")


def validate_image_file(file_obj) -> None:
    """Validate uploaded images (size + extension + MIME + Pillow verify when available)."""
    _validate_size(file_obj, max_bytes=MAX_IMAGE_BYTES, label_ar="الصورة")

    name = (getattr(file_obj, "name", "") or "").lower()
    _validate_ext(name, allowed_exts=ALLOWED_IMAGE_EXTS, label_ar="الصورة")

    content_type = (getattr(file_obj, "content_type", "") or "").lower()
    sniffed = _sniff_mime(file_obj, name)
    if content_type and not content_type.startswith("image/"):
        raise ValidationError("يُسمح برفع الصور فقط.")
    if sniffed and not sniffed.startswith("image/"):
        raise ValidationError("الملف المرفوع ليس صورة صالحة.")
    if sniffed in BLOCKED_MIME_PREFIXES:
        raise ValidationError("نوع الملف غير مسموح.")

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


def validate_attachment_file(file_obj) -> None:
    """Validate generic attachments (pdf/images/doc/docx)."""
    _validate_size(file_obj, max_bytes=MAX_ATTACHMENT_BYTES, label_ar="المرفق")
    name = (getattr(file_obj, "name", "") or "").lower()
    _validate_ext(name, allowed_exts=ALLOWED_ATTACHMENT_EXTS, label_ar="المرفق")

    sniffed = _sniff_mime(file_obj, name)
    if sniffed in BLOCKED_MIME_PREFIXES:
        raise ValidationError("نوع الملف غير مسموح.")


def validate_circular_attachment_file(file_obj) -> None:
    """Validate circular attachments (PDF/images only)."""
    _validate_size(file_obj, max_bytes=MAX_ATTACHMENT_BYTES, label_ar="المرفق")
    name = (getattr(file_obj, "name", "") or "").lower()
    _validate_ext(name, allowed_exts=ALLOWED_CIRCULAR_ATTACHMENT_EXTS, label_ar="المرفق")

    sniffed = _sniff_mime(file_obj, name)
    if sniffed in BLOCKED_MIME_PREFIXES:
        raise ValidationError("نوع الملف غير مسموح.")

    # Allow only PDF or images by MIME when we can detect it.
    if sniffed and not (sniffed == "application/pdf" or sniffed.startswith("image/")):
        raise ValidationError("يُسمح برفع PDF أو صور فقط.")


def validate_pdf_file(file_obj) -> None:
    _validate_size(file_obj, max_bytes=MAX_ATTACHMENT_BYTES, label_ar="الملف")
    name = (getattr(file_obj, "name", "") or "").lower()
    ext = _get_ext(name)
    if ext in BLOCKED_EXTS:
        raise ValidationError("امتداد الملف غير مسموح.")
    if ext and ext != ".pdf":
        raise ValidationError("يُسمح برفع ملفات PDF فقط.")

    sniffed = _sniff_mime(file_obj, name)
    if sniffed and sniffed != "application/pdf":
        raise ValidationError("الملف المرفوع ليس PDF صالحًا.")
