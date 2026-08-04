from __future__ import annotations

from decimal import Decimal


# ── Anchor pricing ──────────────────────────────────────────────────
# Only these plans exist in the database. Customer-facing prices for the
# capacities in between (30, 35, 40, 45, 60, 70, 80, 90) are interpolated from
# them — see reports/flexible_pricing.py.
#
# INVARIANT: every paid anchor must carry identical entitlements
# (support_level, onboarding_sessions, included_archive_storage_gb).
# Because the price curve is smooth but an entitlement step is not, any
# difference creates a band where buying MORE capacity costs less in real
# terms. Concretely, when the annual 100 anchor bundled 50 GB of archive, a
# school buying 90 seats paid 2,790 with no archive while 100 seats cost 2,990
# and included storage worth 399 — so the larger purchase was 199 cheaper.
# Archive storage and training sessions are add-ons with their own prices
# (DEFAULT_ARCHIVE_PRICING / DEFAULT_SERVICE_PRICING), available to every
# capacity on equal terms. A regression test enforces this invariant.


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
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل كامل للمدرسة حتى 25 معلماً\n"
            "التقارير والإنجاز والطلبات والتعاميم وPDF\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "انطلاقة | 6 أشهر",
        "price": Decimal("799.00"),
        "days_duration": 180,
        "max_teachers": 25,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل كامل للمدرسة حتى 25 معلماً\n"
            "التقارير والإنجاز والطلبات والتعاميم وPDF\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "انطلاقة | سنوي",
        "price": Decimal("1290.00"),
        "days_duration": 365,
        "max_teachers": 25,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل سنوي كامل حتى 25 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "تشغيل | شهري",
        "price": Decimal("229.00"),
        "days_duration": 30,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "الأنسب لغالبية المدارس حتى 50 معلماً\n"
            "جميع أدوات التوثيق والتشغيل والصلاحيات\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "تشغيل | 6 أشهر",
        "price": Decimal("1190.00"),
        "days_duration": 180,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "الأنسب لغالبية المدارس حتى 50 معلماً\n"
            "جميع أدوات التوثيق والتشغيل والصلاحيات\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "تشغيل | سنوي",
        "price": Decimal("1990.00"),
        "days_duration": 365,
        "max_teachers": 50,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "أفضل قيمة لتشغيل المدرسة حتى 50 معلماً\n"
            "جميع أدوات التشغيل والتوثيق دون تجزئة\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "قيادة | شهري",
        "price": Decimal("349.00"),
        "days_duration": 30,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل قيادي موسع حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "قيادة | 6 أشهر",
        "price": Decimal("1790.00"),
        "days_duration": 180,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل قيادي موسع حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
        ),
    },
    {
        "name": "قيادة | سنوي",
        "price": Decimal("2990.00"),
        "days_duration": 365,
        "max_teachers": 100,
        "support_level": "priority",
        "onboarding_sessions": 0,
        "included_archive_storage_gb": 0,
        "description": (
            "تشغيل سنوي قيادي حتى 100 معلم\n"
            "جميع الأدوات والصلاحيات ومسارات الاعتماد\n"
            "دعم بأولوية وجميع مزايا المنصة دون تجزئة"
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


# ── What a paid subscription includes ───────────────────────────────
# Single source of truth for the "what you get" panel shown before payment.
# Identical for every capacity and period by design (see the invariant above),
# so the manager sees the same commitment regardless of the size they pick.
SUBSCRIPTION_INCLUDED_FEATURES = (
    {
        "icon": "fa-file-lines",
        "title": "التقارير اليومية والأسبوعية",
        "detail": "إنشاء وتوثيق التقارير بالصور، مع الطباعة وروابط المشاركة المؤقتة.",
    },
    {
        "icon": "fa-award",
        "title": "ملفات إنجاز المعلمين",
        "detail": "ملف إنجاز لكل معلم بشواهد ومرفقات، جاهز للطباعة والتصدير PDF.",
    },
    {
        "icon": "fa-bullhorn",
        "title": "التعاميم والتوقيع الإلكتروني",
        "detail": "إرسال التعاميم ومتابعة من وقّع ومن لم يوقّع، مع تذكير تلقائي.",
    },
    {
        "icon": "fa-headset",
        "title": "الطلبات والدعم الداخلي",
        "detail": "نظام طلبات وتذاكر داخل المدرسة مع توجيهها لمسؤولي الأقسام.",
    },
    {
        "icon": "fa-chart-line",
        "title": "لوحة المدير والتقرير الدوري",
        "detail": "مؤشرات المدرسة لحظياً، مع ملخص دوري يصل مدير المدرسة تلقائياً.",
    },
    {
        "icon": "fa-user-shield",
        "title": "صلاحيات وأدوار كاملة",
        "detail": "مدير، مسؤول قسم، معلم، ومشرف تقارير (عرض فقط) — كل دور ببياناته.",
    },
    {
        "icon": "fa-mobile-screen",
        "title": "تطبيق ويب مثبَّت على الجوال",
        "detail": "يعمل كتطبيق على الجوال والحاسب مع إشعارات فورية.",
    },
    {
        "icon": "fa-headphones",
        "title": "دعم فني بأولوية",
        "detail": "قناة دعم مباشرة طوال مدة الاشتراك دون رسوم إضافية.",
    },
)

# Sold separately, on equal terms for every capacity. Listed next to the
# included features so the manager is not surprised after paying.
SUBSCRIPTION_ADDON_NOTES = (
    "أرشيف السنة الدراسية (خدمة سنوية مستقلة).",
    "مساحة تخزين إضافية للأرشيف عند الحاجة.",
    "جلسات تدريب وإعداد إضافية عند طلبها.",
)
