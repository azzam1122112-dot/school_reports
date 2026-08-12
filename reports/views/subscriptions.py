# reports/views/subscriptions.py
# -*- coding: utf-8 -*-
"""واجهةُ توافق لوحدات الفوترة بعد تقسيمها.

كان هذا الملف 4337 سطراً تجمع أربعة نطاقات لا يجمعها إلا أنها تُذكر مع كلمة
«اشتراك»: نواةُ الفوترة، ومكتبُ مالك المنصة، وبوّابتا الدفع، وشاشاتُ العميل.

وقد قُسِّم إلى ``billing_core`` و``billing_platform`` و``billing_gateways``
و``billing_school``. ويبقى هذا الاسم لأن ``reports/views/__init__.py`` يستورد
منه بالنجمة، ولأن ``urls.py`` وعشرات الاختبارات تشير إلى ``views.<name>`` —
وتغييرُ ذلك كان سيخلط إعادةَ الهيكلة بتغييرٍ في السطح العام.

**عند إضافة شاشة فوترة جديدة:** ضعها في وحدتها بحسب نطاقها، لا هنا.
"""

from .billing_core import *  # noqa: F401,F403
from .billing_platform import *  # noqa: F401,F403
from .billing_gateways import *  # noqa: F401,F403
from .billing_school import *  # noqa: F401,F403
