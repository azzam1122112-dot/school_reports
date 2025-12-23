import threading
from django.db import transaction
from django.conf import settings
from django.apps import apps
from django.db.models import Q
import logging

logger = logging.getLogger(__name__)

def run_task_safe(task_func, *args, force_thread: bool = False, **kwargs):
    """
    محاولة تشغيل المهمة عبر Celery، وإذا فشل (بسبب عدم وجود Redis مثلاً) 
    يتم تشغيلها في Thread خلفي لضمان عدم توقف النظام.
    """
    def _thread_wrapper(func, *f_args, **f_kwargs):
        from django.db import connections
        # إغلاق أي اتصالات قديمة موروثة لضمان فتح اتصال جديد نظيف في هذا الـ Thread
        connections.close_all()
        try:
            func(*f_args, **f_kwargs)
        finally:
            # إغلاق الاتصال بعد الانتهاء لتجنب تسريب الاتصالات (Connection Leaks)
            connections.close_all()

    def _execute():
        # في بيئة التطوير (DEBUG=True)، نفضل استخدام Thread مباشرة لتجنب مشاكل عدم تشغيل Worker.
        # ويمكن إجبار الـ Thread صراحةً عبر force_thread=True لبعض المهام الحرجة (مثل توليد PDF).
        _force_thread = bool(force_thread) or bool(getattr(settings, 'DEBUG', False))
        
        if not _force_thread:
            try:
                # محاولة الإرسال لـ Celery
                task_func.delay(*args, **kwargs)
                logger.info(f"Task {task_func.__name__} queued via Celery.")
                return
            except Exception as e:
                logger.warning(f"Celery failed: {e}. Falling back to Thread for {task_func.__name__}.")

        # Fallback or forced thread
        thread = threading.Thread(target=_thread_wrapper, args=(task_func, *args), kwargs=kwargs)
        thread.daemon = True
        thread.start()

    # تنفيذ العملية بعد التأكد من حفظ البيانات في قاعدة البيانات
    transaction.on_commit(_execute)

def _resolve_department_for_category(cat, school=None):
    """يستخرج كائن القسم المرتبط بالتصنيف (إن وُجد) مع مراعاة عزل المدارس.

    عند وجود أكثر من مدرسة، قد تكون نفس أنواع التقارير/العلاقات موجودة في أكثر من مدرسة.
    لذلك إن كان لدينا school (أو كان cat مرتبطًا بحقل school) سنحاول أولاً حل القسم داخل هذه المدرسة،
    ثم نسمح بالرجوع لقسم عام (school=NULL) كخيار احتياطي.
    """
    Department = apps.get_model('reports', 'Department')
    if not cat or Department is None:
        return None

    school_scope = school
    try:
        if school_scope is None:
            school_scope = getattr(cat, "school", None)
    except Exception:
        school_scope = school

    # 1) علاقة مباشرة cat.department (إن وُجدت)
    try:
        d = getattr(cat, "department", None)
        if d:
            if school_scope is not None and hasattr(d, "school"):
                try:
                    ds = getattr(d, "school", None)
                    # إن كان القسم يخص مدرسة أخرى، نتجاهله
                    if ds is not None and ds != school_scope:
                        d = None
                except Exception:
                    d = None
            if d:
                return d
    except Exception:
        pass

    # 2) علاقات M2M شائعة: departments / depts / dept_list
    for rel_name in ("departments", "depts", "dept_list"):
        rel = getattr(cat, rel_name, None)
        if rel is not None:
            try:
                qs = rel.all()
                if school_scope is not None and hasattr(Department, "school"):
                    # نفضّل قسم المدرسة، ثم قسم عام
                    d = qs.filter(school=school_scope).first() or qs.filter(school__isnull=True).first()
                else:
                    d = qs.first()
                if d:
                    return d
            except Exception:
                pass

    # 3) استعلام احتياطي
    try:
        qs = Department.objects.filter(reporttypes=cat)
        if school_scope is not None and hasattr(Department, "school"):
            return qs.filter(school=school_scope).first() or qs.filter(school__isnull=True).first()
        return qs.first()
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
    
    run_task_safe(send_notification_task, n.pk, teacher_ids)
    return n
