from __future__ import annotations

from decimal import Decimal


DEFAULT_SUBSCRIPTION_PLANS = (
    {
        "name": "التجربة المجانية",
        "price": Decimal("0.00"),
        "days_duration": 30,
        "max_teachers": 5,
        "description": (
            "تجربة حقيقية لمدة 30 يومًا داخل المدرسة\n"
            "جميع أدوات التقارير والإنجاز والطلبات\n"
            "بدء مباشر دون بطاقة ائتمانية"
        ),
    },
    {
        "name": "الأساسية | 6 أشهر",
        "price": Decimal("699.00"),
        "days_duration": 180,
        "max_teachers": 25,
        "description": (
            "تشغيل كامل للمدرسة حتى 25 معلماً\n"
            "التقارير وملفات الإنجاز والتعاميم والطلبات\n"
            "PDF ومشاركة آمنة ودعم فني"
        ),
    },
    {
        "name": "الأساسية | سنوية",
        "price": Decimal("1099.00"),
        "days_duration": 365,
        "max_teachers": 25,
        "description": (
            "تشغيل سنوي كامل حتى 25 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "جلسة إعداد مجانية وسعر سنوي أفضل"
        ),
    },
    {
        "name": "الاحترافية | 6 أشهر",
        "price": Decimal("999.00"),
        "days_duration": 180,
        "max_teachers": 50,
        "description": (
            "الأنسب لغالبية المدارس حتى 50 معلماً\n"
            "التقارير والإنجاز والطلبات والتعاميم والتوقيعات\n"
            "PDF ومشاركة آمنة ودعم فني"
        ),
    },
    {
        "name": "الاحترافية | سنوية",
        "price": Decimal("1599.00"),
        "days_duration": 365,
        "max_teachers": 50,
        "description": (
            "أفضل قيمة لتشغيل المدرسة حتى 50 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "جلسة إعداد مجانية وتوفير 399 ريالاً"
        ),
    },
    {
        "name": "الموسعة | 6 أشهر",
        "price": Decimal("1499.00"),
        "days_duration": 180,
        "max_teachers": 100,
        "description": (
            "تشغيل موسع للمدارس الكبيرة حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "PDF ومشاركة آمنة ودعم فني"
        ),
    },
    {
        "name": "الموسعة | سنوية",
        "price": Decimal("2399.00"),
        "days_duration": 365,
        "max_teachers": 100,
        "description": (
            "تشغيل سنوي موسع حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "جلسة إعداد مجانية وتوفير 599 ريالاً"
        ),
    },
)


DEFAULT_ARCHIVE_PRICING = {
    "annual_price": Decimal("399.00"),
    "included_storage_gb": 50,
    "storage_block_gb": 50,
    "storage_block_price": Decimal("149.00"),
    "free_storage_mb": 1024,
}
