# -*- coding: utf-8 -*-
"""بحث الوثائق ونطاق رؤيتها.

**النطاق قبل البحث لا بعده.** الفلترة الأمنية تُطبَّق على الاستعلام الأساس ثم
تُبنى فوقها مرشّحات المستخدم — فلا يستطيع مرشّحٌ في الطلب أن يوسّع ما ضاقت به
الصلاحية. والترتيب المعاكس يجعل كل مرشّح جديد ثغرةً محتملة.
"""
from __future__ import annotations

from django.db.models import Q

from . import capabilities as caps
from .model_parts.approvals import ApprovalState
from .model_parts.documents import Document
from .permissions import capability_source, is_school_manager, supervised_department_ids

__all__ = ["visible_documents", "apply_document_filters", "document_facets"]


def visible_documents(user, school):
    """الوثائق التي يحق لهذا المستخدم رؤيتها في هذه المدرسة.

    ثلاث دوائر:

    - **المدير** يرى كل وثائق مدرسته.
    - **من مُنح الأرشفة** يرى وثائق أقسام نطاقه، **والمؤرشَف منها في كل
      المدرسة**: الأرشيف المعتمَد مرجعٌ مشترك، وحصرُه في القسم يجعل كل قسم
      يعيد إنشاء ما لدى غيره.
    - **من سواهم** يرى وثائقه هو، والمؤرشَف المعتمَد. فالوثيقة قيد المراجعة
      شأنُ صاحبها ومراجعِها وحدهما.
    """
    base = Document.objects.filter(school=school).select_related(
        "owner", "department", "decided_by"
    )
    if is_school_manager(user, active_school=school):
        return base

    archived = Q(approval_state=ApprovalState.APPROVED)
    mine = Q(owner=user)

    if capability_source(user, caps.ARCHIVE_DOCUMENTS, school) is not None:
        supervised = supervised_department_ids(user, school)
        if supervised:
            return base.filter(mine | archived | Q(department_id__in=supervised))

    return base.filter(mine | archived)


def apply_document_filters(queryset, *, year: str = "", kind: str = "", department: str = "", term: str = ""):
    """مرشّحات المستخدم — تُبنى فوق النطاق لا بدلاً منه."""
    year = (year or "").strip()
    kind = (kind or "").strip()
    department = (department or "").strip()
    term = (term or "").strip()

    if year:
        queryset = queryset.filter(academic_year=year)
    if kind and kind in {value for value, _label in Document.Kind.choices}:
        queryset = queryset.filter(kind=kind)
    if department.isdigit():
        queryset = queryset.filter(department_id=int(department))
    if term:
        queryset = queryset.filter(
            Q(title__icontains=term) | Q(description__icontains=term)
        )
    return queryset


def document_facets(queryset) -> dict:
    """محاور التصنيف المتاحة فعلاً في النتائج.

    مشتقّة من الوثائق الموجودة لا من كل القيم الممكنة: قائمةُ سنوات تعرض
    سنواتٍ لا وثيقة فيها تجعل المستخدم يبحث في فراغ.
    """
    years = sorted(
        {
            value
            for value in queryset.values_list("academic_year", flat=True)
            if (value or "").strip()
        },
        reverse=True,
    )
    return {"years": years}
