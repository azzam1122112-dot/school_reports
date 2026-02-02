import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.cache import cache

# مسح الكاش
cache.clear()
print('✅ تم مسح الكاش بنجاح')
print('الآن قم بتحديث صفحة لوحة مدير النظام لرؤية البيانات الصحيحة')
