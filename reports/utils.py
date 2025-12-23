from django.conf import settings
from django.apps import apps
from django.db.models import Q
import logging

logger = logging.getLogger(__name__)

def _resolve_department_for_category(cat):
    """يستخرج كائن القسم المرتبط بالتصنيف (إن وُجد)."""
    Department = apps.get_model('reports', 'Department')
    if not cat or Department is None:
        return None

    # 1) علاقة مباشرة cat.department (إن وُجدت)
    try:
        d = getattr(cat, "department", None)
        if d:
            return d
    except Exception:
        pass

    # 2) علاقات M2M شائعة: departments / depts / dept_list
    for rel_name in ("departments", "depts", "dept_list"):
        rel = getattr(cat, rel_name, None)
        if rel is not None:
            try:
                d = rel.all().first()
                if d:
                    return d
            except Exception:
                pass

    # 3) استعلام احتياطي
    try:
        return Department.objects.filter(reporttypes=cat).first()
    except Exception:
        return None

def _build_head_decision(dept):
    """
    يُرجع dict يحدّد ماذا نطبع في خانة (اعتماد رئيس القسم).
    """
    DepartmentMembership = apps.get_model('reports', 'DepartmentMembership')
    if not dept or DepartmentMembership is None:
        return {"no_render": True}

    try:
        role_officer = getattr(DepartmentMembership, "OFFICER", "officer")
        qs = (DepartmentMembership.objects
              .select_related("teacher")
              .filter(department=dept, role_type=role_officer, teacher__is_active=True))
        heads = [m.teacher for m in qs]
    except Exception:
        heads = []

    count = len(heads)
    policy = getattr(settings, "PRINT_MULTIHEAD_POLICY", "blank")  # "blank" أو "dept"

    if count == 1:
        return {"single": True, "name": getattr(heads[0], "name", str(heads[0]))}

    if policy == "dept":
        return {"multi_dept": True, "dept_name": getattr(dept, "name", "")}

    return {"multi_blank": True}

def create_system_notification(title, message, school=None, teacher_ids=None, is_important=False):
    """
    Helper to create a notification and trigger the background task to send it.
    """
    from .models import Notification
    from .tasks import send_notification_task
    from django.db import transaction

    n = Notification.objects.create(
        title=title,
        message=message,
        school=school,
        is_important=is_important
    )
    
    transaction.on_commit(lambda: send_notification_task.delay(n.pk, teacher_ids))
    return n
