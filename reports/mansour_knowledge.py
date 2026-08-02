from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


AUDIENCE_GENERAL = "general"
AUDIENCE_TEACHER = "teacher"
AUDIENCE_MANAGER = "manager"
AUDIENCE_SUPERVISOR = "supervisor"
AUDIENCE_REPORT_SUPERVISOR = "report_supervisor"
AUDIENCE_PLATFORM_SUPERVISOR = "platform_supervisor"

PUBLIC_AUDIENCES = frozenset(
    {
        AUDIENCE_GENERAL,
        AUDIENCE_TEACHER,
        AUDIENCE_MANAGER,
        AUDIENCE_SUPERVISOR,
    }
)

AUDIENCE_LABELS = {
    AUDIENCE_GENERAL: "زائر",
    AUDIENCE_TEACHER: "معلم",
    AUDIENCE_MANAGER: "مدير مدرسة",
    AUDIENCE_SUPERVISOR: "مشرف",
    AUDIENCE_REPORT_SUPERVISOR: "مشرف تقارير",
    AUDIENCE_PLATFORM_SUPERVISOR: "مشرف منصة",
}

_FALLBACK_ROLE_GUIDANCE = {
    AUDIENCE_GENERAL: (
        "المستخدم لم يحدد دوره بعد. أجب عن المعلومات المشتركة فقط، وإذا اختلفت "
        "الخطوات حسب الصلاحية فاطلب منه تحديد ما إذا كان معلمًا أو مدير مدرسة أو مشرفًا."
    ),
    AUDIENCE_TEACHER: (
        "خاطب المستخدم بصفته معلمًا. ركز على تقاريره وملف إنجازه وطلباته وما يصله "
        "من إشعارات وتعاميم. لا ترشده إلى وظائف إدارة المدرسة أو الإرسال الجماعي."
    ),
    AUDIENCE_MANAGER: (
        "خاطب المستخدم بصفته مدير مدرسة. اشرح إدارة فريق المدرسة والأقسام والتقارير "
        "والطلبات والتعاميم والإشعارات والاشتراك والأرشيف والتخزين ضمن المدرسة النشطة."
    ),
    AUDIENCE_SUPERVISOR: (
        "المستخدم زائر اختار «مشرف» دون تحديد النوع. ميّز بين مشرف تقارير داخل مدرسة "
        "ومشرف منصة يتابع مدارس ضمن نطاقه، واطلب تحديد النوع عندما تختلف الخطوات."
    ),
    AUDIENCE_REPORT_SUPERVISOR: (
        "خاطب المستخدم بصفته مشرف تقارير داخل مدرسة. صلاحياته للعرض والمتابعة فقط؛ "
        "لا تنسب إليه صلاحيات التعديل أو الحذف أو الإرسال أو إدارة الاشتراك."
    ),
    AUDIENCE_PLATFORM_SUPERVISOR: (
        "خاطب المستخدم بصفته مشرف منصة. نطاقه يقتصر على المدارس المخصصة له، ودوره "
        "للمراجعة والتواصل حسب الصلاحيات؛ لا تنسب إليه صلاحيات مدير المدرسة."
    ),
}


@dataclass(frozen=True)
class KnowledgeItem:
    slug: str
    title: str
    url: str
    text: str
    topics: tuple[str, ...]
    audiences: frozenset[str] = frozenset()
    keywords: str = ""
    priority: int = 0
    next_action: str = ""


_FALLBACK_KNOWLEDGE_ITEMS = (
    KnowledgeItem(
        slug="getting-started",
        title="البدء وتسجيل الدخول",
        url="/guide/#start",
        text=(
            "يبدأ المستخدم بتسجيل الدخول برقم الجوال أو الهوية وكلمة المرور. إذا كان "
            "مرتبطًا بأكثر من مدرسة يختار المدرسة التي يريد العمل عليها، ثم يتأكد من "
            "اسم المدرسة النشطة قبل إنشاء أي تقرير أو طلب أو إرسال أي محتوى."
        ),
        topics=("دخول", "بدء", "مدرسة", "حساب"),
        keywords="تسجيل دخول جوال هوية كلمة مرور اختيار تبديل المدرسة البداية",
        priority=4,
    ),
    KnowledgeItem(
        slug="trial-and-registration",
        title="التجربة والتسجيل",
        url="/register/",
        text=(
            "يمكن للمدرسة إنشاء حساب وبدء التجربة المجانية دون بطاقة ائتمانية. ينشئ "
            "مدير المدرسة الحساب، ثم يضيف فريق المدرسة ويجرب رحلة العمل قبل طلب تفعيل "
            "الباقة المناسبة."
        ),
        topics=("تسجيل", "تجربة", "اشتراك"),
        keywords="تسجيل حساب تجربة مجانية بدء بطاقة ائتمانية انشاء مدرسة",
        priority=3,
    ),
    KnowledgeItem(
        slug="plans-and-pricing",
        title="الباقات والأسعار",
        url="/#pricing",
        text=(
            "تظهر الباقات النشطة والأسعار المعتمدة في قسم الباقات بالصفحة الرئيسية. "
            "تختلف الباقات بحسب المدة وسعة المعلمين، ويظهر المبلغ النهائي قبل إرسال طلب الدفع."
        ),
        topics=("باقة", "سعر", "اشتراك", "سعة"),
        keywords="باقات اسعار أسعار سعر ريال مدة معلمين تجديد اشتراك",
        priority=3,
    ),
    KnowledgeItem(
        slug="account-security",
        title="الحساب والأمان",
        url="/guide/#account-security",
        text=(
            "يجب عدم مشاركة كلمة المرور أو بيانات الدخول. يمكن تفعيل الدخول بالبصمة "
            "على الجهاز الشخصي، ويجب تسجيل الخروج عند استخدام جهاز مشترك. التوقيع على "
            "التعاميم مرتبط بحساب المستخدم."
        ),
        topics=("أمان", "حساب", "بصمة", "كلمة مرور"),
        keywords="امان أمان حماية كلمة المرور بصمة passkey خروج حساب اختراق",
        priority=2,
    ),
    KnowledgeItem(
        slug="privacy-and-isolation",
        title="الخصوصية وعزل المدارس",
        url="/privacy/",
        text=(
            "بيانات كل مدرسة معزولة عن المدارس الأخرى، وتتحكم المدرسة النشطة وعضوية "
            "المستخدم وصلاحياته في الوصول. لا ينبغي إرسال أسماء الطلاب أو كلمات المرور "
            "أو الملفات المدرسية الحساسة إلى المساعد الذكي."
        ),
        topics=("خصوصية", "صلاحية", "بيانات"),
        keywords="خصوصية عزل مدارس بيانات حساسة طلاب صلاحيات حماية",
        priority=3,
    ),
    KnowledgeItem(
        slug="support-and-troubleshooting",
        title="المساعدة وحل المشكلات",
        url="/guide/#help",
        text=(
            "عند غياب صفحة أو مدرسة يتأكد المستخدم من المدرسة النشطة وصلاحية حسابه. "
            "وعند تعذر الحفظ يراجع الحقول المنبهة، وعند تعذر رفع صورة يجرب ملفًا واحدًا "
            "بصيغة شائعة واتصالًا مستقرًا. يمكن بعد ذلك استخدام الدعم الفني."
        ),
        topics=("مشكلة", "دعم", "رفع", "حفظ", "صلاحية"),
        keywords="مشكلة خطأ لا يظهر حفظ رفع صورة جلسة صلاحية دعم مساعدة",
        priority=2,
    ),
    KnowledgeItem(
        slug="teacher-workspace",
        title="مساحة عمل المعلم",
        url="/guide/#teacher-home",
        text=(
            "تجمع الصفحة الرئيسية للمعلم آخر تقاريره وملف الإنجاز والتعاميم والإشعارات "
            "والطلبات. يبدأ من البطاقات المختصرة ويراجع تنبيهات الجرس للوصول إلى المحتوى الجديد."
        ),
        topics=("معلم", "رئيسية", "تنبيهات"),
        audiences=frozenset({AUDIENCE_TEACHER}),
        keywords="معلم الصفحة الرئيسية بطاقات جرس تنبيهات يومي",
        priority=4,
    ),
    KnowledgeItem(
        slug="teacher-reports",
        title="إنشاء تقارير المعلم",
        url="/guide/#teacher-report",
        text=(
            "يفتح المعلم «إضافة تقرير»، ثم يدخل عنوان النشاط وتاريخه ونوعه وعدد "
            "المستفيدين ووصفًا مختصرًا، ويرفق الصور المناسبة ويراجعها قبل الحفظ. يظهر "
            "التقرير بعد الحفظ في «تقاريري» ويمكن مراجعته وطباعته أو مشاركته حسب المتاح."
        ),
        topics=("تقرير", "صور", "نشاط", "طباعة", "مشاركة"),
        audiences=frozenset({AUDIENCE_TEACHER}),
        keywords="اضافة إضافة تقرير تقاريري نشاط تاريخ نوع مستفيدين وصف صور حفظ طباعة مشاركة تعديل",
        priority=5,
    ),
    KnowledgeItem(
        slug="teacher-achievement",
        title="ملف إنجاز المعلم",
        url="/guide/#teacher-achievement",
        text=(
            "يختار المعلم السنة الدراسية وينشئ ملف الإنجاز، ثم يضيف أعماله بعناوين "
            "واضحة ويرفق الشواهد المتاحة. بعد المراجعة يمكن الطباعة أو تنزيل PDF أو "
            "إنشاء رابط مشاركة مؤقت."
        ),
        topics=("إنجاز", "شواهد", "PDF", "مشاركة"),
        audiences=frozenset({AUDIENCE_TEACHER}),
        keywords="ملف انجاز إنجاز سنة دراسية اعمال أعمال شواهد مرفقات pdf رابط مشاركة",
        priority=5,
    ),
    KnowledgeItem(
        slug="teacher-circulars",
        title="التعاميم والإشعارات للمعلم",
        url="/guide/#teacher-circulars",
        text=(
            "يقرأ المعلم الإشعار أو التعميم الوارد إليه. الإشعار للمعلومة السريعة، "
            "أما التعميم فقد يتطلب تفعيل الإقرار ثم تأكيد التوقيع. المعلم لا ينشئ "
            "تعميمات المدرسة؛ الإرسال من صلاحيات مدير المدرسة أو مشرف المنصة بحسب النطاق."
        ),
        topics=("تعميم", "إشعار", "توقيع", "قراءة"),
        audiences=frozenset({AUDIENCE_TEACHER}),
        keywords="تعميم تعاميم اشعار إشعار وارد قراءة غير مقروء توقيع اقرار إقرار",
        priority=5,
    ),
    KnowledgeItem(
        slug="teacher-requests",
        title="طلبات المعلم",
        url="/guide/#teacher-requests",
        text=(
            "ينشئ المعلم طلبًا بتحديد النوع وكتابة عنوان ووصف واضحين، ثم يتابع حالته "
            "من «طلباتي». تظهر الحالات والملاحظات المضافة حتى اكتمال المعالجة."
        ),
        topics=("طلب", "حالة", "ملاحظات"),
        audiences=frozenset({AUDIENCE_TEACHER}),
        keywords="طلب جديد طلباتي تذكرة حالة متابعة ملاحظات مكتمل معالجة",
        priority=4,
    ),
    KnowledgeItem(
        slug="manager-dashboard",
        title="لوحة مدير المدرسة",
        url="/guide/#manager-dashboard",
        text=(
            "تعرض لوحة المدير مؤشرات المدرسة وآخر النشاطات وروابط الوصول السريع إلى "
            "التقارير والمعلمين والطلبات. جميع عمليات المدير تُحصر في المدرسة النشطة."
        ),
        topics=("مدير", "لوحة", "مؤشرات", "متابعة"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="مدير مدرسة لوحة المدير داشبورد مؤشرات نشاطات متابعة",
        priority=5,
    ),
    KnowledgeItem(
        slug="manager-team",
        title="إدارة المعلمين والأقسام",
        url="/guide/#manager-teachers",
        text=(
            "يستطيع مدير المدرسة إضافة المعلمين أو استيرادهم بالنموذج الجاهز، وتحديث "
            "الحسابات وتنظيم الأقسام وإضافة أعضائها وتحديد مسؤول القسم. يجب مراجعة رقم "
            "الجوال والمدرسة قبل الحفظ، وتخضع أعداد المعلمين لسعة الباقة."
        ),
        topics=("معلمين", "أقسام", "استيراد", "مسؤول"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="ادارة إدارة معلمين اضافة إضافة استيراد اكسل Excel قسم اقسام أقسام مسؤول عضو حذف تعديل جوال",
        priority=5,
    ),
    KnowledgeItem(
        slug="manager-reports",
        title="تقارير المدرسة للمدير",
        url="/guide/#manager-reports",
        text=(
            "تجمع صفحة تقارير المدرسة أعمال الفريق. يستطيع المدير البحث والتصفية "
            "باسم المعلم أو نوع التقرير أو الفترة الزمنية، ثم فتح التقرير لمراجعة "
            "تفاصيله وصوره وطباعته أو مشاركته عند الحاجة."
        ),
        topics=("تقارير", "بحث", "تصفية", "مراجعة"),
        audiences=frozenset({AUDIENCE_MANAGER, AUDIENCE_REPORT_SUPERVISOR}),
        keywords="تقارير المدرسة بحث فلترة تصفية معلم تاريخ نوع مراجعة صور طباعة",
        priority=5,
    ),
    KnowledgeItem(
        slug="manager-communication",
        title="إرسال الإشعارات والتعاميم",
        url="/guide/#manager-communication",
        text=(
            "يستخدم المدير الإشعار للمعلومة السريعة، والتعميم عندما يحتاج إلى توثيق "
            "القراءة أو التوقيع. يمكنه اختيار معلمين محددين أو قسم واحد أو عدة أقسام، "
            "ويراجع عدد المستلمين قبل الإرسال. من صفحة المرسلة يتابع القراءة والتوقيعات."
        ),
        topics=("إشعار", "تعميم", "مستلمين", "قسم", "توقيع"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="ارسال إرسال اشعار إشعار تعميم مستلمين معلمين اختيار قسم اقسام أقسام قراءة توقيعات موعد",
        priority=6,
    ),
    KnowledgeItem(
        slug="manager-requests",
        title="إدارة طلبات المدرسة",
        url="/guide/#manager-requests",
        text=(
            "يستقبل المدير طلبات الفريق، ويمكن إسناد الطلب للمسؤول المناسب وتحديث "
            "حالته وإضافة الملاحظات حتى الإغلاق. التحديث المكتوب يوضح لصاحب الطلب ما تم."
        ),
        topics=("طلبات", "إسناد", "معالجة", "إغلاق"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="طلبات المدرسة صندوق الوارد اسناد إسناد مسؤول معالجة حالة اغلاق إغلاق ملاحظة",
        priority=4,
    ),
    KnowledgeItem(
        slug="manager-subscription",
        title="اشتراك المدرسة والمدفوعات",
        url="/guide/#manager-subscription",
        text=(
            "تعرض صفحة اشتراك المدرسة الباقة الحالية وحالتها وتاريخ الانتهاء وسعة "
            "المعلمين وسجل المدفوعات. يستطيع المدير اختيار خدمات التجديد ورفع إيصال "
            "الدفع، ويصبح التفعيل بعد اعتماد العملية."
        ),
        topics=("اشتراك", "باقة", "تجديد", "دفع", "إيصال"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="اشتراك المدرسة باقة انتهاء تجديد مدفوعات دفع ايصال إيصال تفعيل",
        priority=5,
    ),
    KnowledgeItem(
        slug="manager-archive",
        title="الأرشيف السنوي للمدرسة",
        url="/archive/",
        text=(
            "الأرشيف السنوي إضافة مستقلة يديرها مدير المدرسة حتى لا يضيع سجل السنة عند الانتقال للسنة التالية. "
            "ينشئ نسخة ثابتة قابلة للتنزيل تشمل "
            "التقارير وملفات الإنجاز والطلبات والتعاميم والإشعارات وملفاتها المرتبطة، "
            "مع فهرس Excel وبصمة تحقق. تبقى النسخ المحفوظة متاحة بعد انتهاء الإضافة."
        ),
        topics=("أرشيف", "نسخة", "سنة", "تصدير"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="ارشيف أرشيف سنوي نسخة ثابتة بيانات مدرسة تقارير انجاز طلبات تعاميم اشعارات بصمة تنزيل",
        priority=6,
    ),
    KnowledgeItem(
        slug="manager-storage",
        title="مساحة التخزين وزيادتها",
        url="/subscription/my/#archiveOrder",
        text=(
            "تحتسب المنصة استخدام المدرسة الفعلي من صور التقارير وشواهد الإنجاز "
            "وملفات الطلبات ومرفقات التعاميم والإشعارات ونسخ الأرشيف الثابتة. تعرض "
            "صفحة الاشتراك المستخدم والحد والمتبقي والتفصيل. يستطيع المدير طلب سعة "
            "إضافية، وتُضاف إلى الحد الحالي بعد اعتماد دفعتها."
        ),
        topics=("تخزين", "مساحة", "سعة", "زيادة", "أرشيف"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="مساحة تخزين سعة مستخدم متبقي حد زيادة جيجا GB ارشيف أرشيف مرفقات",
        priority=6,
    ),
    KnowledgeItem(
        slug="manager-export",
        title="تصدير بيانات المدرسة",
        url="/staff/export/",
        text=(
            "عند تفعيل الأرشيف يستطيع مدير المدرسة تصدير فهرس Excel منظم لبيانات المدرسة "
            "وتنزيل حزمة كاملة من الملفات المرتبطة. التصدير مخصص للمدير ويقتصر على المدرسة النشطة."
        ),
        topics=("تصدير", "تنزيل", "بيانات", "ملفات"),
        audiences=frozenset({AUDIENCE_MANAGER}),
        keywords="تصدير بيانات مدرسة اكسل Excel zip ملفات تنزيل نسخة احتياطية",
        priority=4,
    ),
    KnowledgeItem(
        slug="report-supervisor",
        title="مشرف التقارير داخل المدرسة",
        url="/guide/#report-supervisor",
        text=(
            "مشرف التقارير عضوية مدرسية للعرض والمتابعة فقط. يمكنه مراجعة تقارير "
            "المدرسة التي تسمح بها عضويته، ولا يملك صلاحيات مدير المدرسة مثل إدارة "
            "المعلمين أو حذف البيانات أو إرسال التعاميم أو إدارة الاشتراك والأرشيف."
        ),
        topics=("مشرف تقارير", "عرض", "متابعة", "صلاحية"),
        audiences=frozenset(
            {AUDIENCE_SUPERVISOR, AUDIENCE_REPORT_SUPERVISOR}
        ),
        keywords="مشرف تقارير عرض فقط متابعة صلاحيات مدرسة قراءة بدون تعديل حذف ارسال",
        priority=7,
    ),
    KnowledgeItem(
        slug="platform-supervisor",
        title="مشرف المنصة ونطاق المدارس",
        url="/guide/#supervisor",
        text=(
            "يرى مشرف المنصة دليل المدارس الواقعة ضمن نطاقه، ويختار مدرسة لمراجعة "
            "لوحتها وتقاريرها وطلباتها وفق الصلاحيات المتاحة. يجب التأكد من المدرسة "
            "الحالية، ولا يتحول مشرف المنصة إلى مدير للمدرسة ولا يتجاوز نطاقه."
        ),
        topics=("مشرف منصة", "مدارس", "نطاق", "متابعة"),
        audiences=frozenset(
            {AUDIENCE_SUPERVISOR, AUDIENCE_PLATFORM_SUPERVISOR}
        ),
        keywords="مشرف منصة مشرف عام دليل المدارس نطاق مدن بنين بنات دخول مدرسة متابعة",
        priority=7,
    ),
    KnowledgeItem(
        slug="platform-communication",
        title="تواصل مشرف المنصة",
        url="/circulars/create/",
        text=(
            "يستطيع مشرف المنصة إرسال التواصل إلى مديري المدارس الواقعة ضمن نطاقه "
            "بحسب صلاحياته، مع اختيار مدرسة أو مدراء محددين أو الإرسال للنطاق المتاح. "
            "لا يرسل مباشرة إلى معلمي مدرسة بصفته مديرًا لها."
        ),
        topics=("مشرف منصة", "تعميم", "مدراء", "نطاق"),
        audiences=frozenset(
            {AUDIENCE_SUPERVISOR, AUDIENCE_PLATFORM_SUPERVISOR}
        ),
        keywords="مشرف منصة ارسال إرسال تعميم مدراء المدارس نطاق كل المدارس مدير محدد",
        priority=6,
    ),
)


_FALLBACK_ROLE_DEFAULT_SLUGS = {
    AUDIENCE_GENERAL: (
        "getting-started",
        "trial-and-registration",
        "plans-and-pricing",
        "support-and-troubleshooting",
    ),
    AUDIENCE_TEACHER: (
        "teacher-workspace",
        "teacher-reports",
        "teacher-achievement",
        "teacher-circulars",
    ),
    AUDIENCE_MANAGER: (
        "manager-dashboard",
        "manager-team",
        "manager-communication",
        "manager-subscription",
    ),
    AUDIENCE_SUPERVISOR: (
        "report-supervisor",
        "platform-supervisor",
        "platform-communication",
        "getting-started",
    ),
    AUDIENCE_REPORT_SUPERVISOR: (
        "report-supervisor",
        "manager-reports",
        "getting-started",
        "support-and-troubleshooting",
    ),
    AUDIENCE_PLATFORM_SUPERVISOR: (
        "platform-supervisor",
        "platform-communication",
        "getting-started",
        "support-and-troubleshooting",
    ),
}


_KNOWLEDGE_CONTENT_PATH = Path(__file__).with_name("mansour_knowledge_content.json")


def _load_content_payload() -> dict[str, Any]:
    try:
        raw = _KNOWLEDGE_CONTENT_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except Exception as exc:
        logger.warning("Mansour knowledge file load failed: %s", exc.__class__.__name__)
    return {}


def _coerce_knowledge_items(payload: Any) -> tuple[KnowledgeItem, ...]:
    if not isinstance(payload, list):
        return _FALLBACK_KNOWLEDGE_ITEMS

    items: list[KnowledgeItem] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        text = str(row.get("text") or "").strip()
        if not (slug and title and text):
            continue

        raw_topics = row.get("topics")
        if isinstance(raw_topics, list):
            topics = tuple(str(item).strip() for item in raw_topics if str(item).strip())
        else:
            topics = ()

        raw_audiences = row.get("audiences")
        if isinstance(raw_audiences, list):
            audiences = frozenset(
                value
                for value in (str(item).strip() for item in raw_audiences)
                if value in AUDIENCE_LABELS
            )
        else:
            audiences = frozenset()

        try:
            priority = int(row.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0

        items.append(
            KnowledgeItem(
                slug=slug,
                title=title,
                url=url,
                text=text,
                topics=topics,
                audiences=audiences,
                keywords=str(row.get("keywords") or "").strip(),
                priority=priority,
                next_action=str(row.get("next_action") or "").strip(),
            )
        )

    return tuple(items) if items else _FALLBACK_KNOWLEDGE_ITEMS


def _coerce_role_guidance(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return _FALLBACK_ROLE_GUIDANCE

    resolved = dict(_FALLBACK_ROLE_GUIDANCE)
    for audience in AUDIENCE_LABELS:
        value = payload.get(audience)
        if isinstance(value, str) and value.strip():
            resolved[audience] = value.strip()
    return resolved


def _coerce_role_defaults(payload: Any, available_slugs: set[str]) -> dict[str, tuple[str, ...]]:
    if not isinstance(payload, dict):
        return _FALLBACK_ROLE_DEFAULT_SLUGS

    resolved: dict[str, tuple[str, ...]] = dict(_FALLBACK_ROLE_DEFAULT_SLUGS)
    for audience in AUDIENCE_LABELS:
        raw_items = payload.get(audience)
        if not isinstance(raw_items, list):
            continue
        selected = tuple(
            slug
            for slug in (str(item).strip() for item in raw_items)
            if slug and slug in available_slugs
        )
        if selected:
            resolved[audience] = selected
    return resolved


_content_payload = _load_content_payload()
KNOWLEDGE_ITEMS = _coerce_knowledge_items(_content_payload.get("knowledge_items"))
ROLE_GUIDANCE = _coerce_role_guidance(_content_payload.get("role_guidance"))
ROLE_DEFAULT_SLUGS = _coerce_role_defaults(
    _content_payload.get("role_default_slugs"),
    {item.slug for item in KNOWLEDGE_ITEMS},
)
