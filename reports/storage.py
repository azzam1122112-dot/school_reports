# reports/storage.py
# -*- coding: utf-8 -*-
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.exceptions import ImproperlyConfigured

# نحاول استيراد Cloudinary، لكن نستخدمه فقط إذا كان مفعلًا في الإعدادات
try:
    from cloudinary_storage.storage import RawMediaCloudinaryStorage
except (ImportError, ImproperlyConfigured):
    RawMediaCloudinaryStorage = None

# نتحقق هل Cloudinary مفعل في settings.py؟
_use_cloudinary = (
    RawMediaCloudinaryStorage is not None
    and hasattr(settings, "CLOUDINARY_STORAGE")
    and settings.CLOUDINARY_STORAGE
)

if _use_cloudinary:
    class PublicRawMediaStorage(RawMediaCloudinaryStorage):
        """
        تخزين Cloudinary للملفات العامة كـ RAW (PDF/DOCX/ZIP..).
        - يرفع الملفات تحت resource_type="raw"
        - الوصول عام (type=upload)
        """
        pass
else:
    # في بيئة التطوير المحلية (بدون Cloudinary) نستخدم التخزين المحلي العادي
    class PublicRawMediaStorage(FileSystemStorage):
        pass

