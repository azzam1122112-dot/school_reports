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
        "name": "انطلاقة | شهري",
        "price": Decimal("149.00"),
        "days_duration": 30,
        "max_teachers": 25,
        "support_level": "standard",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل كامل للمدرسة حتى 25 معلماً\n"
            "التقارير والإنجاز والطلبات والتعاميم وPDF\n"
            "دعم فني اعتيادي وإعداد ذاتي مرن"
        ),
    },
    {
        "name": "انطلاقة | 6 أشهر",
        "price": Decimal("799.00"),
        "days_duration": 180,
        "max_teachers": 25,
        "support_level": "standard",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل كامل للمدرسة حتى 25 معلماً\n"
            "التقارير والإنجاز والطلبات والتعاميم وPDF\n"
            "دعم فني اعتيادي وسعر نصف سنوي أفضل"
        ),
    },
    {
        "name": "انطلاقة | سنوي",
        "price": Decimal("1290.00"),
        "days_duration": 365,
        "max_teachers": 25,
        "support_level": "standard",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل سنوي كامل حتى 25 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "دعم فني اعتيادي وأفضل قيمة سنوية"
        ),
    },
    {
        "name": "تشغيل | شهري",
        "price": Decimal("229.00"),
        "days_duration": 30,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 1,
        "included_archive_storage_gb": 0,
        "description": (
            "الأنسب لغالبية المدارس حتى 50 معلماً\n"
            "جميع أدوات التوثيق والتشغيل والصلاحيات\n"
            "جلسة إعداد ومساعدة استيراد ودعم بأولوية"
        ),
    },
    {
        "name": "تشغيل | 6 أشهر",
        "price": Decimal("1190.00"),
        "days_duration": 180,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 1,
        "included_archive_storage_gb": 0,
        "description": (
            "الأنسب لغالبية المدارس حتى 50 معلماً\n"
            "جميع أدوات التوثيق والتشغيل والصلاحيات\n"
            "جلسة إعداد ومساعدة استيراد ودعم بأولوية"
        ),
    },
    {
        "name": "تشغيل | سنوي",
        "price": Decimal("1990.00"),
        "days_duration": 365,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 1,
        "included_archive_storage_gb": 0,
        "description": (
            "أفضل قيمة لتشغيل المدرسة حتى 50 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "جلسة إعداد ومساعدة استيراد ودعم بأولوية"
        ),
    },
    {
        "name": "قيادة | شهري",
        "price": Decimal("349.00"),
        "days_duration": 30,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 2,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل قيادي موسع حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "جلستان للتدريب والإعداد ودعم أولوية"
        ),
    },
    {
        "name": "قيادة | 6 أشهر",
        "price": Decimal("1790.00"),
        "days_duration": 180,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 2,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل قيادي موسع حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "جلستان للتدريب والإعداد ودعم أولوية"
        ),
    },
    {
        "name": "قيادة | سنوي",
        "price": Decimal("2990.00"),
        "days_duration": 365,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 2,
        "included_archive_storage_gb": 50,
        "description": (
            "تشغيل سنوي قيادي حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "جلستان للتدريب ودعم أولوية وأرشيف 50GB"
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


DEFAULT_SERVICE_PRICING = {
    "extra_teachers_count": 25,
    "extra_teachers_annual_price": Decimal("399.00"),
    "training_session_price": Decimal("249.00"),
}
