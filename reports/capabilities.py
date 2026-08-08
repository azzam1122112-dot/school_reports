# -*- coding: utf-8 -*-
"""مرجع الصلاحيات القابلة للمنح داخل المدرسة.

**لماذا في الكود لا في قاعدة البيانات؟** لأن الصلاحية سلوك، والسلوك يعيش في
الكود. جدولُ صلاحيات في القاعدة يسمح بإنشاء صف لا يقرؤه أي سطر — فيرى المدير
خياراً يمنحه ويظنه نافذاً وهو لا يفعل شيئاً. أما هنا فلا وجود لصلاحية إلا
ومعها الموضع الذي يفحصها.

**قوالب معتمدة لا خانات حرة.** توصيف الأدوار يشترط «منح الصلاحيات وفق القوالب
المعتمدة»، وهو الشرط الصحيح هندسياً أيضاً: عشرون خانةً مستقلة تنتج ملايين
التوليفات التي لا يمكن اختبارها ولا شرحها لمستخدم. القالب يختصرها إلى بضع حالات
مفهومة، ويبقى التعديل الفردي متاحاً فوقه لمن يحتاجه.

**الصلاحية المعلَّقة تُعلَن.** بعض ما يطلبه التوصيف يعتمد على كيانات لم تُبنَ
بعد (التكليفات، الاجتماعات، الخطط). فبدل إخفائها — فيبدو النظام ناقصاً بلا
تفسير — أو منحها وهي جوفاء — فيُخدع المدير — تُعرَّف هنا بعلامة ``available``
وتظهر في الشاشة معطّلةً بوسم «قريباً».
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    code: str
    label: str
    description: str
    # المجموعة التي تُعرض تحتها في الشاشة.
    group: str
    # هل الميزة التي تفحص هذه الصلاحية مبنيّة فعلاً؟
    available: bool = True
    # الأدوار التي يجوز منحها هذه الصلاحية.
    roles: tuple[str, ...] = ("deputy", "admin_staff")


# ─────────────────────────────────────────────────────────────────────────────
# التعريفات
# ─────────────────────────────────────────────────────────────────────────────
VIEW_SCHOOL_DASHBOARD = "view_school_dashboard"
REVIEW_REPORTS = "review_reports"
RECOMMEND_APPROVAL = "recommend_approval"
VIEW_ACHIEVEMENTS = "view_achievements"
HANDLE_REQUESTS = "handle_requests"
DRAFT_CIRCULARS = "draft_circulars"
VIEW_AUDIT_LOG = "view_audit_log"
ASSIGN_TASKS = "assign_tasks"
MANAGE_MEETINGS = "manage_meetings"
TRACK_PLANS = "track_plans"
ARCHIVE_DOCUMENTS = "archive_documents"
MANAGE_LAB = "manage_lab"

ALL: tuple[Capability, ...] = (
    # ── المتابعة والاطلاع ────────────────────────────────────────────────
    Capability(
        VIEW_SCHOOL_DASHBOARD,
        "الاطلاع على لوحة المدرسة",
        "يرى مؤشرات المدرسة ضمن نطاقه دون بيانات خارج إشرافه.",
        group="متابعة",
    ),
    Capability(
        VIEW_ACHIEVEMENTS,
        "متابعة ملفات إنجاز المنسوبين",
        "يطّلع على ملفات إنجاز من يقعون تحت إشرافه دون اعتمادها.",
        group="متابعة",
    ),
    Capability(
        VIEW_AUDIT_LOG,
        "الاطلاع على سجل الإجراءات",
        "يرى سجل الإجراءات في نطاق اختصاصه وحده.",
        group="متابعة",
    ),
    # ── المراجعة والتوصية ────────────────────────────────────────────────
    Capability(
        REVIEW_REPORTS,
        "مراجعة التقارير وإعادتها",
        "يراجع تقارير نطاقه ويعيدها لمُعدّها بملاحظة، دون اعتماد نهائي.",
        group="مراجعة",
    ),
    Capability(
        RECOMMEND_APPROVAL,
        "التوصية بالاعتماد",
        "يرفع العمل لمدير المدرسة موصياً باعتماده. الاعتماد النهائي يبقى للمدير.",
        group="مراجعة",
    ),
    Capability(
        HANDLE_REQUESTS,
        "متابعة الطلبات المحالة إليه",
        "يتابع الطلبات في نطاقه ويوصي بالموافقة أو الرفض أو الاستكمال.",
        group="مراجعة",
    ),
    # ── الإعداد والتنفيذ ─────────────────────────────────────────────────
    Capability(
        DRAFT_CIRCULARS,
        "إعداد مسودات التعاميم",
        "يُعدّ مسودة تعميم ويرسلها للمراجعة. النشر يبقى بيد المدير.",
        group="إعداد",
    ),
    Capability(
        ASSIGN_TASKS,
        "توزيع التكليفات",
        "يوزّع التكليفات على من يشرف عليهم ضمن نطاقه.",
        group="إعداد",
        roles=("deputy",),
    ),
    Capability(
        MANAGE_MEETINGS,
        "تنظيم الاجتماعات وكتابة المحاضر",
        "ينظّم اجتماعات نطاقه ويكتب محاضرها. الاعتماد يبقى للمدير.",
        group="إعداد",
    ),
    Capability(
        TRACK_PLANS,
        "متابعة تنفيذ الخطة",
        "يتابع مهام الخطة والمبادرات المسندة إلى نطاقه.",
        group="إعداد",
        roles=("deputy",),
    ),
    Capability(
        ARCHIVE_DOCUMENTS,
        "تصنيف الوثائق ورفعها للأرشيف",
        "يصنّف وثائق نطاقه ويرفعها إلى الأرشيف. اعتماد النقل يبقى للمدير.",
        group="إعداد",
    ),
    # متابعة المختبر ليست من عمل المحضّر: هو صاحب العهدة والتجارب بحكم مسمّاه
    # الوظيفي لا بحكم صلاحية تُمنح. وهذه الصلاحية لمن **يشرف عليه** — وكيلاً كان
    # أو موظفاً إدارياً — فيقرأ الجرد ويراجع التجارب ويوصي، ولا يسجّل نيابةً عنه.
    Capability(
        MANAGE_LAB,
        "متابعة المختبر وتجاربه",
        "يطّلع على عهدة المختبر وتجاربه ويراجعها. الاعتماد النهائي يبقى للمدير.",
        group="متابعة",
    ),
)

BY_CODE: dict[str, Capability] = {item.code: item for item in ALL}
VALID_CODES: frozenset[str] = frozenset(BY_CODE)
AVAILABLE_CODES: frozenset[str] = frozenset(item.code for item in ALL if item.available)


# ─────────────────────────────────────────────────────────────────────────────
# القوالب المعتمدة
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Template:
    code: str
    label: str
    description: str
    role: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)


TEMPLATES: tuple[Template, ...] = (
    Template(
        "deputy_academic",
        "وكيل الشؤون التعليمية",
        "متابعة تقارير المعلمين وملفات إنجازهم والبرامج التعليمية، ومراجعتها والتوصية باعتمادها.",
        role="deputy",
        capabilities=(
            VIEW_SCHOOL_DASHBOARD,
            VIEW_ACHIEVEMENTS,
            VIEW_AUDIT_LOG,
            REVIEW_REPORTS,
            RECOMMEND_APPROVAL,
            ASSIGN_TASKS,
            TRACK_PLANS,
            MANAGE_MEETINGS,
            # المختبر شأنٌ تعليمي: تجاربه تخدم مقررات المواد، فمتابعته من وكيل
            # الشؤون التعليمية لا من وكيل الشؤون المدرسية.
            MANAGE_LAB,
        ),
    ),
    Template(
        "deputy_operations",
        "وكيل الشؤون المدرسية",
        "الإشراف على الأعمال الإدارية والخدمات المساندة والأرشفة، ومتابعة الموظفين ضمن نطاقه.",
        role="deputy",
        capabilities=(
            VIEW_SCHOOL_DASHBOARD,
            VIEW_AUDIT_LOG,
            REVIEW_REPORTS,
            RECOMMEND_APPROVAL,
            HANDLE_REQUESTS,
            ASSIGN_TASKS,
            ARCHIVE_DOCUMENTS,
            MANAGE_MEETINGS,
            DRAFT_CIRCULARS,
        ),
    ),
    Template(
        "deputy_students",
        "وكيل شؤون الطلاب",
        "الخصائص العامة في المنصة دون أي شاشة تخص الطلاب — فالمنصة لا تدير بيانات الطلاب.",
        role="deputy",
        capabilities=(
            VIEW_SCHOOL_DASHBOARD,
            VIEW_AUDIT_LOG,
            REVIEW_REPORTS,
            RECOMMEND_APPROVAL,
            ASSIGN_TASKS,
            MANAGE_MEETINGS,
            TRACK_PLANS,
        ),
    ),
    Template(
        "deputy_shared",
        "وكيل بصلاحيات مشتركة",
        "كل ما يجوز منحه لوكيل. يُستعمل في المدارس الصغيرة التي لا تفصل الوكالات.",
        role="deputy",
        capabilities=tuple(
            item.code for item in ALL if "deputy" in item.roles
        ),
    ),
    Template(
        "admin_staff_basic",
        "موظف إداري (الأساسي)",
        "تنفيذ التكليفات وإعداد التقارير ورفع الشواهد. لا يملك أي صلاحية مراجعة أو اعتماد.",
        role="admin_staff",
        capabilities=(),
    ),
    Template(
        "admin_staff_documents",
        "موظف إداري — الوثائق والتعاميم",
        "الأساسي، مضافاً إليه إعداد مسودات التعاميم وتصنيف الوثائق للأرشيف.",
        role="admin_staff",
        capabilities=(DRAFT_CIRCULARS, ARCHIVE_DOCUMENTS, MANAGE_MEETINGS),
    ),
)

TEMPLATES_BY_CODE: dict[str, Template] = {item.code: item for item in TEMPLATES}


def templates_for_role(role: str) -> tuple[Template, ...]:
    return tuple(item for item in TEMPLATES if item.role == role)


def capabilities_for_role(role: str) -> tuple[Capability, ...]:
    return tuple(item for item in ALL if role in item.roles)


def sanitize(codes, *, role: str | None = None) -> list[str]:
    """يُبقي الرموز المعروفة وحدها، بالترتيب المُعرَّف في :data:`ALL`.

    الترتيب الثابت مقصود: قائمة تُخزَّن بترتيب إدخال المستخدم تجعل سجلّين
    متطابقين في المعنى مختلفين في التخزين، فتُظهر المقارنةُ فرقاً لا وجود له.
    """
    incoming = {str(code).strip() for code in (codes or []) if str(code).strip()}
    allowed = VALID_CODES if role is None else {item.code for item in capabilities_for_role(role)}
    return [item.code for item in ALL if item.code in incoming and item.code in allowed]


def grouped_for_role(role: str) -> list[tuple[str, list[Capability]]]:
    """الصلاحيات مجمّعة حسب ``group`` — بالترتيب الذي تظهر به في الشاشة."""
    groups: dict[str, list[Capability]] = {}
    for item in capabilities_for_role(role):
        groups.setdefault(item.group, []).append(item)
    return list(groups.items())
