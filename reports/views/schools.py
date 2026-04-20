# reports/views/schools.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
from django.db.models import Count, Q

from ._helpers import *
from ._helpers import (
    _is_staff, _role_display_map, _filter_by_school,
    _model_has_field, _get_active_school, _user_manager_schools,
    _clean_query_value, _parse_date_safe,
)


# ========= دعم الأقسام =========
def _dept_code_for(dept_obj_or_code) -> str:
    if hasattr(dept_obj_or_code, "slug") and getattr(dept_obj_or_code, "slug"):
        return getattr(dept_obj_or_code, "slug")
    if hasattr(dept_obj_or_code, "code") and getattr(dept_obj_or_code, "code"):
        return getattr(dept_obj_or_code, "code")
    return str(dept_obj_or_code or "").strip()

def _arabic_label_for_in_school(dept_obj_or_code, active_school: Optional[School] = None) -> str:
    """نسخة آمنة من _arabic_label_for تربط التسمية بالمدرسة النشطة لتجنب تداخل slugs بين المدارس."""
    if hasattr(dept_obj_or_code, "name") and getattr(dept_obj_or_code, "name"):
        return dept_obj_or_code.name
    code = (
        getattr(dept_obj_or_code, "slug", None)
        or getattr(dept_obj_or_code, "code", None)
        or (dept_obj_or_code if isinstance(dept_obj_or_code, str) else "")
    )
    return _role_display_map(active_school).get(code, code or "—")

def _resolve_department_by_code_or_pk(code_or_pk: str, school: Optional[School] = None) -> Tuple[Optional[object], str, str]:
    dept_obj = None
    dept_code = (code_or_pk or "").strip()

    if Department is not None:
        try:
            qs = Department.objects.all()
            if school is not None and _model_has_field(Department, "school"):
                qs = qs.filter(school=school)
            dept_obj = qs.filter(slug__iexact=dept_code).first()
            if not dept_obj:
                try:
                    dept_obj = qs.filter(pk=int(dept_code)).first()
                except (ValueError, TypeError):
                    dept_obj = None
        except Exception:
            dept_obj = None

        if dept_obj:
            dept_code = getattr(dept_obj, "slug", dept_code)

    dept_label = _arabic_label_for_in_school(dept_obj or dept_code, school)
    return dept_obj, dept_code, dept_label

def _members_for_department(dept_code: str, school: Optional[School] = None):
    if not dept_code:
        return Teacher.objects.none()
    if DepartmentMembership is None:
        return Teacher.objects.none()

    mem_qs = DepartmentMembership.objects.filter(department__slug__iexact=dept_code)
    if school is not None:
        mem_qs = mem_qs.filter(department__school=school)
    member_ids = mem_qs.values_list("teacher_id", flat=True)

    qs = Teacher.objects.filter(is_active=True, id__in=member_ids).distinct()
    if school is not None:
        qs = qs.filter(
            school_memberships__school=school,
        )
    return qs.order_by("name")

def _user_department_codes(user, active_school: Optional[School] = None) -> list[str]:
    codes = set()

    # في وضع تعدد المدارس، يجب تحديد المدرسة النشطة لتجنب تداخل slugs بين المدارس
    try:
        if active_school is None and School.objects.filter(is_active=True)[:2].count() > 1:
            return []
    except Exception:
        # fail-closed إذا تعذر تحديد عدد المدارس
        if active_school is None:
            return []

    if DepartmentMembership is not None:
        try:
            mem_qs = DepartmentMembership.objects.filter(teacher=user)
            if active_school is not None:
                mem_qs = mem_qs.filter(department__school=active_school)
            mem_codes = mem_qs.values_list("department__slug", flat=True)
            for c in mem_codes:
                if c:
                    codes.add(c)
        except Exception:
            logger.exception("Failed to fetch user department codes")

    return list(codes)

def _tickets_stats_for_department(dept_code: str, school: Optional[School] = None) -> dict:
    from django.db.models import Count, Q as _Q
    qs = Ticket.objects.filter(department__slug=dept_code)
    qs = _filter_by_school(qs, school)
    stats = qs.aggregate(
        open=Count("id", filter=_Q(status="open")),
        in_progress=Count("id", filter=_Q(status="in_progress")),
        done=Count("id", filter=_Q(status="done")),
    )
    return stats

def _all_departments(active_school: Optional[School] = None):
    if Department is None:
        return []

    qs = Department.objects.all().order_by("id")
    if active_school is not None and hasattr(Department, "school"):
        qs = qs.filter(school=active_school)

    departments = list(qs)
    if not departments:
        return []

    department_ids = [d.pk for d in departments if getattr(d, "pk", None) is not None]
    department_codes = [_dept_code_for(d) for d in departments]

    ticket_stats_map = defaultdict(lambda: {"open": 0, "in_progress": 0, "done": 0})
    if Ticket is not None and department_ids:
        try:
            ticket_qs = Ticket.objects.filter(department_id__in=department_ids)
            if active_school is not None and _model_has_field(Ticket, "school"):
                ticket_qs = ticket_qs.filter(school=active_school)
            ticket_rows = ticket_qs.values("department_id").annotate(
                open_count=Count("id", filter=Q(status=Ticket.Status.OPEN)),
                in_progress_count=Count("id", filter=Q(status=Ticket.Status.IN_PROGRESS)),
                done_count=Count("id", filter=Q(status=Ticket.Status.DONE)),
            )
            for row in ticket_rows:
                ticket_stats_map[row["department_id"]] = {
                    "open": int(row.get("open_count") or 0),
                    "in_progress": int(row.get("in_progress_count") or 0),
                    "done": int(row.get("done_count") or 0),
                }
        except Exception:
            logger.exception("Failed to batch ticket stats for departments list")

    membership_teacher_ids_by_department = defaultdict(set)
    if DepartmentMembership is not None and department_ids:
        try:
            membership_rows = DepartmentMembership.objects.filter(
                department_id__in=department_ids
            ).values_list("department_id", "teacher_id")
            for department_id, teacher_id in membership_rows:
                if department_id and teacher_id:
                    membership_teacher_ids_by_department[int(department_id)].add(int(teacher_id))
        except Exception:
            logger.exception("Failed to batch department memberships for departments list")

    items = []
    for department in departments:
        code = _dept_code_for(department)
        stats = ticket_stats_map.get(department.pk) or {"open": 0, "in_progress": 0, "done": 0}
        member_ids = membership_teacher_ids_by_department.get(int(department.pk), set())
        members_count = len(member_ids)

        items.append(
            {
                "pk": department.pk,
                "slug": code,
                "code": code,
                "name": _arabic_label_for_in_school(department, active_school),
                "is_active": getattr(department, "is_active", True),
                "members_count": members_count,
                "stats": stats,
                "tickets_summary": f"{stats['open']} / {stats['in_progress']} / {stats['done']}",
            }
        )

    return items

class _DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields: list[str] = []
        if model is not None:
            for fname in ("name", "slug", "role_label", "is_active"):
                if hasattr(model, fname):
                    fields.append(fname)

    def clean(self):
        cleaned = super().clean()
        return cleaned

def get_department_form():
    if Department is not None and 'DepartmentForm' in globals() and (DepartmentForm is not None):
        return DepartmentForm
    if Department is not None:
        return _DepartmentForm
    return None


# ---- إعدادات المدرسة الحالية (للمدير أو المشرف العام) ----
class _SchoolSettingsForm(forms.ModelForm):
    years_text = forms.CharField(
        label="السنوات الدراسية المتاحة (هجري)",
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "1446-1447, 1447-1448 ..."}),
        help_text="أدخل السنوات المسموحة مفصولة بفاصلة. اتركها فارغة لاستخدام الوضع الافتراضي."
    )

    class Meta:
        model = School
        fields = [
            "name",
            "stage",
            "gender",
            "city",
            "phone",
            "share_link_default_days",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.allowed_academic_years:
            self.fields["years_text"].initial = ", ".join(self.instance.allowed_academic_years)

    def clean_years_text(self):
        import re
        data = self.cleaned_data.get("years_text", "")
        if not data:
            return []
        years = []
        for part in data.replace("،", ",").split(","):
            p = part.strip()
            if not p:
                continue
            if not re.match(r"^\d{4}-\d{4}$", p):
                 # يمكن تجاهل غير الصالح أو رفع خطأ. سنرفض الخطأ لتنبيه المستخدم.
                pass 
            years.append(p)
        
        # ترتيبها
        years.sort()
        return years

    def save(self, commit=True):
        self.instance.allowed_academic_years = self.cleaned_data["years_text"]
        return super().save(commit=commit)


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_settings(request: HttpRequest) -> HttpResponse:
    """إعدادات المدرسة الحالية (الاسم، الشعار...).

    - متاحة لمدير المدرسة على مدرسته النشطة فقط.
    - متاحة للمشرف العام على أي مدرسة بعد اختيارها كـ active_school.
    """
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    # تحقق من الصلاحيات
    if not (getattr(request.user, "is_superuser", False) or active_school in _user_manager_schools(request.user)):
        messages.error(request, "لا تملك صلاحية تعديل إعدادات هذه المدرسة.")
        return redirect("reports:admin_dashboard")

    # حماية جزئية: منع التعديل على الحقول المطلوبة فقط.
    protected_fields = {"name", "stage", "gender", "city"}
    form = _SchoolSettingsForm(request.POST or None, request.FILES or None, instance=active_school)

    for field_name, field in form.fields.items():
        if field_name in protected_fields:
            field.disabled = True
            attrs = dict(getattr(field.widget, "attrs", {}) or {})
            attrs["disabled"] = True
            attrs["readonly"] = True
            field.widget.attrs = attrs

    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث إعدادات المدرسة بنجاح.")
            return redirect("reports:admin_dashboard")
        # في حال وجود أخطاء نعرضها للمستخدم ليسهل معرفة سبب الفشل
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
        try:
            for field, errors in form.errors.items():
                label = form.fields.get(field).label if field in form.fields else field
                joined = "; ".join(errors)
                messages.error(request, f"{label}: {joined}")
        except Exception:
            # لا نكسر الصفحة إن حدث خطأ أثناء بناء الرسالة
            pass

    return render(
        request,
        "reports/school_settings.html",
        {"form": form, "school": active_school},
    )


# ---- إدارة المدارس (إنشاء/تعديل/حذف) للمشرف العام ----
class _SchoolAdminForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            "name",
            "code",
            "stage",
            "gender",
            "city",
            "phone",
            "is_active",
        ]


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_create(request: HttpRequest) -> HttpResponse:
    form = _SchoolAdminForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء المدرسة بنجاح.")
            return redirect("reports:schools_admin_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/school_form.html", {"form": form, "mode": "create"})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_update(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)
    form = _SchoolAdminForm(request.POST or None, instance=school)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث بيانات المدرسة.")
            return redirect("reports:schools_admin_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/school_form.html", {"form": form, "mode": "edit", "school": school})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def school_delete(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)
    name = school.name
    from ..middleware import set_audit_logging_suppressed

    try:
        set_audit_logging_suppressed(True)
        school.delete()
        messages.success(request, f"🗑️ تم حذف المدرسة «{name}» وكل بياناتها المرتبطة.")
    except Exception:
        logger.exception("school_delete failed")
        messages.error(request, "تعذّر حذف المدرسة. ربما توجد قيود على البيانات المرتبطة.")
    finally:
        set_audit_logging_suppressed(False)
    return redirect("reports:schools_admin_list")


# ---- لوحة إدارة المدارس ومدراء المدارس (للسوبر أدمن) ----
@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET"])
def schools_admin_list(request: HttpRequest) -> HttpResponse:
    schools = (
        School.objects.all()
        .select_related("subscription")
        .order_by("name")
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=SchoolMembership.objects.select_related("teacher").filter(
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                ),
                to_attr="manager_memberships",
            )
        )
    )

    items = []
    for s in schools:
        managers = [m.teacher for m in getattr(s, "manager_memberships", []) if m.teacher]
        items.append({"school": s, "managers": managers})

    return render(request, "reports/schools_admin_list.html", {"schools": items})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def school_profile(request: HttpRequest, pk: int) -> HttpResponse:
    """بروفايل تفصيلي لمدرسة واحدة.

    - السوبر أدمن يمكنه عرض أي مدرسة.
    - مدير المدرسة يمكنه عرض المدارس التي يديرها فقط.
    """
    school = get_object_or_404(School, pk=pk)

    user = request.user
    allowed = False
    if getattr(user, "is_superuser", False):
        allowed = True
    elif _is_staff(user) and school in _user_manager_schools(user):
        allowed = True

    if not allowed:
        messages.error(request, "لا تملك صلاحية عرض هذه المدرسة.")
        return redirect("reports:admin_dashboard")

    # إحصائيات بسيطة للمدرسة
    reports_count = Report.objects.filter(school=school).count()

    # Single aggregate query for all ticket counts instead of 4 separate queries
    from django.db.models import Count, Q as _Q
    ticket_stats = Ticket.objects.filter(school=school).aggregate(
        total=Count("id"),
        open=Count("id", filter=_Q(status__in=["open", "in_progress"])),
        done=Count("id", filter=_Q(status="done")),
        rejected=Count("id", filter=_Q(status="rejected")),
    )
    tickets_total = ticket_stats["total"]
    tickets_open = ticket_stats["open"]
    tickets_done = ticket_stats["done"]
    tickets_rejected = ticket_stats["rejected"]

    teachers_qs = (
        Teacher.objects.filter(
            school_memberships__school=school,
            school_memberships__is_active=True,
        )
        .distinct()
        .order_by("name")
    )
    teachers_count = teachers_qs.count()

    departments_count = 0
    departments = []
    if Department is not None:
        try:
            depts_qs = Department.objects.filter(is_active=True)
            if DepartmentMembership is not None:
                depts_qs = (
                    depts_qs.filter(
                        memberships__teacher__school_memberships__school=school,
                        memberships__teacher__school_memberships__is_active=True,
                    )
                    .distinct()
                    .order_by("name")
                )
            departments_count = depts_qs.count()
            departments = list(depts_qs[:20])  # عرض عينات محدودة في القالب إن لزم
        except Exception:
            logger.exception("school_profile departments stats failed")

    context = {
        "school": school,
        "reports_count": reports_count,
        "tickets_total": tickets_total,
        "tickets_open": tickets_open,
        "tickets_done": tickets_done,
        "tickets_rejected": tickets_rejected,
        "teachers_count": teachers_count,
        "teachers": list(teachers_qs[:20]),  # أقصى 20 للعرض السريع
        "departments_count": departments_count,
        "departments": departments,
    }
    return render(request, "reports/school_profile.html", context)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_managers_manage(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        teacher_id = request.POST.get("teacher_id")
        if not teacher_id:
            messages.error(request, "الرجاء اختيار معلّم.")
            return redirect("reports:school_managers_manage", pk=school.pk)
        try:
            teacher = Teacher.objects.get(pk=teacher_id)
        except Teacher.DoesNotExist:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:school_managers_manage", pk=school.pk)

        if action == "add":
            # لا نسمح بأكثر من مدير نشط واحد لكل مدرسة
            other_manager_exists = SchoolMembership.objects.filter(
                school=school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exclude(teacher=teacher).exists()
            if other_manager_exists:
                messages.error(request, "لا يمكن تعيين أكثر من مدير نشط لنفس المدرسة. قم بإلغاء تعيين المدير الحالي أولاً.")
                return redirect("reports:school_managers_manage", pk=school.pk)

            SchoolMembership.objects.update_or_create(
                school=school,
                teacher=teacher,
                role_type=SchoolMembership.RoleType.MANAGER,
                defaults={"is_active": True},
            )
            messages.success(request, f"تم تعيين {teacher.name} مديراً للمدرسة.")
        elif action == "remove":
            SchoolMembership.objects.filter(
                school=school,
                teacher=teacher,
                role_type=SchoolMembership.RoleType.MANAGER,
            ).update(is_active=False)
            messages.success(request, f"تم إلغاء إدارة {teacher.name} لهذه المدرسة.")
        else:
            messages.error(request, "إجراء غير معروف.")

        return redirect("reports:school_managers_manage", pk=school.pk)

    managers = (
        Teacher.objects.filter(
            school_memberships__school=school,
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .distinct()
        .order_by("name")
    )

    # في قائمة الإضافة نظهر فقط الحسابات التي لديها عضوية إدارة فعلية في مدرسة ما.
    teachers = (
        Teacher.objects.filter(is_active=True)
        .filter(
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .exclude(
            school_memberships__school=school,
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .distinct()
        .order_by("name")
    )

    context = {
        "school": school,
        "managers": managers,
        "teachers": teachers,
    }
    return render(request, "reports/school_managers_manage.html", context)

# ---- لوحة المدير المجمعة ----
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])

def admin_dashboard(request: HttpRequest) -> HttpResponse:
    """لوحة تحكم مدير المدرسة - تحديث Premium 2026"""
    from django.core.cache import cache
    from django.db.models.functions import TruncWeek, TruncMonth
    import json
    
    # إذا لم يكن هناك مدرسة مختارة نوجّه لاختيار مدرسة أولاً
    active_school = _get_active_school(request)
    # السوبر يوزر يمكنه رؤية أي مدرسة، المدير مقيد بمدارسه فقط
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية كمدير على هذه المدرسة.")
            return redirect("reports:select_school")

    # محاولة جلب البيانات من الكاش
    cache_key = f"admin_stats_v2_{active_school.id if active_school else 'global'}"
    try:
        stats = cache.get(cache_key)
    except Exception:
        stats = None

    if not stats:
        # عدد المعلّمين داخل المدرسة النشطة فقط (عزل حسب المدرسة)
        teachers_qs = Teacher.objects.all()
        if active_school is not None:
            teachers_qs = teachers_qs.filter(
                school_memberships__school=active_school,
                school_memberships__is_active=True,
            ).distinct()

        # تجميع عدادات التذاكر في استعلام واحد بدل 4 استعلامات منفصلة
        ticket_base = _filter_by_school(Ticket.objects.filter(is_platform=False), active_school)
        ticket_agg = ticket_base.aggregate(
            total=Count("id"),
            open=Count("id", filter=Q(status__in=["open", "in_progress"])),
            done=Count("id", filter=Q(status="done")),
            rejected=Count("id", filter=Q(status="rejected")),
        )

        stats = {
            "reports_count": _filter_by_school(Report.objects.all(), active_school).count(),
            "teachers_count": teachers_qs.count(),
            "tickets_total": ticket_agg["total"],
            "tickets_open": ticket_agg["open"],
            "tickets_done": ticket_agg["done"],
            "tickets_rejected": ticket_agg["rejected"],
        }
        # تخزين في الكاش لمدة 5 دقائق
        try:
            cache.set(cache_key, stats, 300)
        except Exception:
            pass

    ctx = {
        **stats,
        "has_dept_model": Department is not None,
        "active_school": active_school,
    }

    has_reporttype = False
    reporttypes_count = 0
    try:
        from ..models import ReportType  # type: ignore
        has_reporttype = True

        # نعرض عدد الأنواع المعرّفة (وليس فقط المستخدمة) داخل المدرسة النشطة.
        rt_qs = ReportType.objects.all()
        if hasattr(ReportType, "is_active"):
            rt_qs = rt_qs.filter(is_active=True)
        if active_school is not None and _model_has_field(ReportType, "school"):
            rt_qs = rt_qs.filter(school=active_school)
        reporttypes_count = rt_qs.count()
    except Exception:
        pass

    ctx.update({
        "has_reporttype": has_reporttype,
        "reporttypes_count": reporttypes_count,
    })
    
    # إضافة بيانات الرسوم البيانية والتحليلات المتقدمة
    if active_school:
        charts_cache_key = f"admin_charts_v2_{active_school.id}"
        try:
            charts = cache.get(charts_cache_key)
        except Exception:
            charts = None

        now = timezone.now()
        
        if not charts:
            # تقارير آخر 8 أسابيع
            eight_weeks_ago = now - timedelta(weeks=8)
            reports_by_week = _filter_by_school(
                Report.objects.filter(created_at__gte=eight_weeks_ago), 
                active_school
            ).annotate(
                week=TruncWeek('created_at')
            ).values('week').annotate(
                count=Count('id')
            ).order_by('week')
            
            reports_labels = []
            reports_data = []
            for item in reports_by_week:
                if item['week']:
                    # عرض تاريخ بداية الأسبوع (أوضح من التاريخ الكامل)
                    week_label = item['week'].strftime('%d/%m')
                    reports_labels.append(week_label)
                    reports_data.append(item['count'])
            
            # تقارير حسب التصنيف/النوع
            reports_by_category = _filter_by_school(
                Report.objects.all(), 
                active_school
            ).values('category__name').annotate(
                count=Count('id')
            ).order_by('-count')[:6]
            
            dept_labels = []
            dept_data = []
            for item in reports_by_category:
                category_name = item['category__name'] or 'غير محدد'
                dept_labels.append(category_name)
                dept_data.append(item['count'])
            
            # معلمين حسب القسم
            if Department is not None:
                teachers_by_dept_qs = Department.objects.filter(
                    school=active_school
                ).annotate(
                    teacher_count=Count('memberships__teacher', distinct=True)
                ).order_by('-teacher_count')[:6]
                
                teachers_labels = []
                teachers_data = []
                for dept in teachers_by_dept_qs:
                    teachers_labels.append(dept.name)
                    teachers_data.append(dept.teacher_count)
            else:
                teachers_labels = []
                teachers_data = []
            
            charts = {
                "reports_labels": json.dumps(reports_labels),
                "reports_data": json.dumps(reports_data),
                "dept_labels": json.dumps(dept_labels),
                "dept_data": json.dumps(dept_data),
                "teachers_labels": json.dumps(teachers_labels),
                "teachers_data": json.dumps(teachers_data),
            }
            
            try:
                cache.set(charts_cache_key, charts, 600)  # 10 دقائق
            except Exception:
                pass
        
        ctx.update(charts)
        
        # بيانات الاشتراك والتنبيهات
        subscription_warning = None
        try:
            from ..models import SchoolSubscription
            active_subscription = SchoolSubscription.objects.filter(
                school=active_school,
                is_active=True
            ).first()
            
            if active_subscription:
                days_remaining = (active_subscription.end_date - now.date()).days
                ctx['subscription'] = active_subscription
                ctx['days_remaining'] = days_remaining
                
                if days_remaining <= 7:
                    subscription_warning = 'critical'
                elif days_remaining <= 30:
                    subscription_warning = 'warning'
            else:
                subscription_warning = 'expired'
        except Exception:
            pass
        
        ctx['subscription_warning'] = subscription_warning
        
        # آخر الأنشطة
        recent_activities = []
        try:
            recent_reports = _filter_by_school(
                Report.objects.all(),
                active_school
            ).select_related('teacher', 'category').order_by('-created_at')[:5]
            
            for report in recent_reports:
                teacher_name = getattr(getattr(report, 'teacher', None), 'name', None) or 'معلم'
                category_name = getattr(getattr(report, 'category', None), 'name', None) or 'قسم'
                recent_activities.append({
                    'type': 'report',
                    'icon': 'fa-file-alt',
                    'color': 'primary',
                    'title': 'تقرير جديد',
                    'description': f"{teacher_name} - {category_name}",
                    'time': report.created_at,
                })
            
            recent_tickets = _filter_by_school(
                Ticket.objects.filter(is_platform=False),
                active_school
            ).order_by('-created_at')[:3]
            
            for ticket in recent_tickets:
                ticket_title = (getattr(ticket, 'title', None) or '').strip()
                recent_activities.append({
                    'type': 'ticket',
                    'icon': 'fa-ticket-alt',
                    'color': 'warning',
                    'title': 'طلب جديد',
                    'description': (ticket_title[:50] if ticket_title else 'طلب بدون عنوان'),
                    'time': ticket.created_at,
                })
            
            recent_activities.sort(key=lambda x: x['time'], reverse=True)
            recent_activities = recent_activities[:8]
        except Exception:
            pass
        
        ctx['recent_activities'] = recent_activities

    return render(request, "reports/admin_dashboard.html", ctx)

@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
def school_audit_logs(request: HttpRequest) -> HttpResponse:
    """عرض سجل العمليات الخاص بالمدرسة للمدير."""
    active_school = _get_active_school(request)
    if active_school is None:
        return redirect("reports:select_school")
    
    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية كمدير على هذه المدرسة.")
        return redirect("reports:select_school")

    # ملاحظة: في بعض بيئات النشر قد لا تكون ترحيلات AuditLog مطبّقة بعد.
    # بدلاً من 500، نظهر الصفحة مع تنبيه واضح.
    try:
        from django.db.utils import OperationalError, ProgrammingError
    except Exception:  # pragma: no cover
        OperationalError = Exception  # type: ignore
        ProgrammingError = Exception  # type: ignore

    logs_qs = None
    try:
        logs_qs = AuditLog.objects.filter(school=active_school).select_related("teacher")
    except (OperationalError, ProgrammingError):
        messages.error(
            request,
            "ميزة سجل العمليات غير مفعّلة حالياً (لم يتم تطبيق الترحيلات بعد). "
            "يرجى تشغيل migrate ثم إعادة المحاولة.",
        )

    # تصفية/عرض السجلات (لو كانت متاحة)
    teacher_id = _clean_query_value(request.GET.get("teacher"))
    action = _clean_query_value(request.GET.get("action"))
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    allowed_actions = {value for value, _label in AuditLog.Action.choices}

    if logs_qs is not None:
        if teacher_id.isdigit():
            logs_qs = logs_qs.filter(teacher_id=teacher_id)
        else:
            teacher_id = ""
        if action in allowed_actions:
            logs_qs = logs_qs.filter(action=action)
        else:
            action = ""
        if start_date is not None:
            logs_qs = logs_qs.filter(timestamp__date__gte=start_date)
        if end_date is not None:
            logs_qs = logs_qs.filter(timestamp__date__lte=end_date)

        paginator = Paginator(logs_qs, 50)
        page = request.GET.get("page")
        logs = paginator.get_page(page)
    else:
        # لا نستخدم QuerySet هنا حتى لا نلمس قاعدة البيانات.
        logs = Paginator([], 50).get_page(1)

    # قائمة المعلمين في المدرسة للتصفية
    try:
        teachers = Teacher.objects.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
        ).distinct()
    except Exception:
        teachers = Teacher.objects.none()

    params = request.GET.copy()
    if "page" in params:
        params.pop("page")
    for key in list(params.keys()):
        cleaned = _clean_query_value(params.get(key))
        if cleaned:
            params[key] = cleaned
        else:
            params.pop(key)

    ctx = {
        "logs": logs,
        "teachers": teachers,
        "actions": AuditLog.Action.choices,
        "active_school": active_school,
        "q_teacher": teacher_id,
        "q_action": action,
        "q_start": start_date.isoformat() if start_date else "",
        "q_end": end_date.isoformat() if end_date else "",
        "qs": params.urlencode(),
    }
    return render(request, "reports/audit_logs.html", ctx)


# ---- الأقسام: عرض/إنشاء/تعديل/حذف ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def departments_list(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    depts = _all_departments(active_school)
    return render(
        request,
        "reports/departments_list.html",
        {"departments": depts, "has_dept_model": Department is not None},
    )

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_create(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    FormCls = get_department_form()
    if not (Department is not None and FormCls is not None):
        messages.error(request, "إنشاء الأقسام يتطلب تفعيل موديل Department.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None, active_school=active_school)
    if request.method == "POST":
        if form.is_valid():
            dep = form.save(commit=False)
            if hasattr(dep, "school") and active_school is not None:
                dep.school = active_school
            dep.save()
            # حفظ علاقات M2M بعد الحفظ الأولي
            if hasattr(form, "save_m2m"):
                form.save_m2m()
            messages.success(request, "✅ تم إنشاء القسم.")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "create"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_update(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    FormCls = get_department_form()
    if not (Department is not None and FormCls is not None):
        messages.error(request, "نموذج الأقسام غير مُعد بعد.")
        return redirect("reports:departments_list")
    dep = get_object_or_404(Department, pk=pk, school=active_school)  # type: ignore[arg-type]
    form = FormCls(request.POST or None, instance=dep, active_school=active_school)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "✏️ تم تحديث بيانات القسم.")
        return redirect("reports:departments_list")
    return render(request, "reports/department_form.html", {"form": form, "title": "تعديل قسم", "dep": dep})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_edit(request: HttpRequest, code: str) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    if Department is None:
        messages.error(request, "تعديل الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: منع تعديل قسم يخص مدرسة أخرى.
    # الأقسام العامة (school is NULL) يسمح بها للسوبر فقط.
    try:
        if not getattr(request.user, "is_superuser", False) and hasattr(obj, "school_id"):
            if getattr(obj, "school_id", None) is None:
                messages.error(request, "لا يمكنك تعديل قسم عام.")
                return redirect("reports:departments_list")
            if active_school is None or getattr(obj, "school_id", None) != getattr(active_school, "id", None):
                messages.error(request, "لا يمكنك تعديل قسم من مدرسة أخرى.")
                return redirect("reports:departments_list")
    except Exception:
        pass

    FormCls = get_department_form()
    if not FormCls:
        messages.error(request, "DepartmentForm غير متاح.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None, instance=obj, active_school=active_school)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"✏️ تم تحديث قسم «{label}».")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "edit", "department": obj})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_manager_create(request: HttpRequest) -> HttpResponse:
    """إنشاء حساب مدير مدرسة وربطه بمدرسة واحدة على الأقل (ويمكن بأكثر من مدرسة).

    - يستخدم نموذج مبسّط (ManagerCreateForm) لإنشاء مستخدم مدير.
    - بعد الإنشاء يتم إسناد الدور "manager" وضبط عضويات SchoolMembership كمدير.
    """
    # مدارس متاحة للاختيار
    schools = School.objects.filter(is_active=True).order_by("name")
    initial_school_id = request.GET.get("school_id")

    form = ManagerCreateForm(request.POST or None)
    selected_ids = request.POST.getlist("schools") if request.method == "POST" else ([] if not initial_school_id else [initial_school_id])

    if request.method == "POST":
        if not selected_ids:
            messages.error(request, "يجب ربط المدير بمدرسة واحدة على الأقل.")
        if form.is_valid() and selected_ids:
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)

                    valid_schools = School.objects.filter(id__in=selected_ids, is_active=True)
                    if not valid_schools:
                        raise ValidationError("لا توجد مدارس صالحة للربط.")

                    # منع أكثر من مدير نشط واحد لكل مدرسة
                    conflict_exists = SchoolMembership.objects.filter(
                        school__in=valid_schools,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    ).exists()
                    if conflict_exists:
                        raise ValidationError("إحدى المدارس المختارة لديها مدير نشط بالفعل. لا يمكن تعيين أكثر من مدير واحد للمدرسة.")

                    for s in valid_schools:
                        SchoolMembership.objects.update_or_create(
                            school=s,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.MANAGER,
                            defaults={"is_active": True},
                        )
                messages.success(request, "تم إنشاء حساب مدير/مديرة المدرسة وربطه بالمدارس المحددة.")
                return redirect("reports:schools_admin_list")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except Exception:
                logger.exception("school_manager_create failed")
                messages.error(request, "تعذّر إنشاء حساب مدير/مديرة المدرسة. تحقّق من البيانات وحاول مرة أخرى.")

    context = {
        "form": form,
        "schools": schools,
        "selected_ids": [str(i) for i in selected_ids],
        "mode": "create",
        "manager": None,
    }
    return render(request, "reports/school_manager_create.html", context)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET"])
def school_managers_list(request: HttpRequest) -> HttpResponse:
    """قائمة مدراء المدارس على مستوى المنصة."""
    manager_memberships = SchoolMembership.objects.select_related("school").filter(
        role_type=SchoolMembership.RoleType.MANAGER,
    )
    managers_qs = (
        Teacher.objects.filter(
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER
        )
        .distinct()
        .order_by("name")
        .prefetch_related(
            Prefetch(
                "school_memberships",
                queryset=manager_memberships,
                to_attr="manager_school_memberships",
            )
        )
    )

    items: list[dict] = []
    for t in managers_qs:
        schools = []
        seen_school_ids: set[int] = set()
        for membership in getattr(t, "manager_school_memberships", []):
            school = getattr(membership, "school", None)
            school_id = getattr(school, "id", None)
            if school is None or school_id in seen_school_ids:
                continue
            seen_school_ids.add(int(school_id))
            schools.append(school)
        items.append({"manager": t, "schools": schools})

    return render(request, "reports/school_managers_list.html", {"managers": items})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def school_manager_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """تبديل حالة مدير مدرسة (تفعيل/تعطيل).

    لا نحذف السجل نهائيًا للحفاظ على السجلات المرتبطة.
    """

    manager = get_object_or_404(Teacher, pk=pk)

    try:
        with transaction.atomic():
            if manager.is_active:
                manager.is_active = False
                msg = "🗑️ تم إيقاف حساب المدير وإلغاء صلاحياته في المدارس."
                # عند التعطيل، نعطّل العضويات أيضاً
                SchoolMembership.objects.filter(
                    teacher=manager,
                    role_type=SchoolMembership.RoleType.MANAGER,
                ).update(is_active=False)
            else:
                manager.is_active = True
                msg = "✅ تم إعادة تفعيل حساب المدير بنجاح."
                # ملاحظة: لا نفعّل العضويات تلقائياً هنا لأننا لا نعرف أي مدرسة يجب تفعيلها 
                # يفضل أن يقوم المدير بتعديل المدارس من صفحة التعديل.

            manager.save(update_fields=["is_active"])

        messages.success(request, msg)
    except Exception:
        logger.exception("school_manager_toggle failed")
        messages.error(request, "تعذّر تغيير حالة المدير. حاول لاحقًا.")

    return redirect("reports:school_managers_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_manager_update(request: HttpRequest, pk: int) -> HttpResponse:
    """تعديل بيانات مدير مدرسة موجود باستخدام نفس نموذج الإنشاء.

    - يمكن ترك كلمة المرور فارغة للإبقاء على الحالية.
    - يمكن تغيير المدارس المرتبطة بالمدير.
    """

    manager = get_object_or_404(
        Teacher.objects.prefetch_related("school_memberships__school"),
        pk=pk,
    )

    schools = School.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        form = ManagerCreateForm(request.POST or None, instance=manager)
        selected_ids = request.POST.getlist("schools")
        if not selected_ids:
            messages.error(request, "يجب ربط المدير بمدرسة واحدة على الأقل.")
        if form.is_valid() and selected_ids:
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)
                    valid_schools = School.objects.filter(id__in=selected_ids, is_active=True)

                    # منع أكثر من مدير نشط واحد لكل مدرسة: نسمح فقط إن كانت المدرسة
                    # بدون مدير أو أن المدير الحالي هو نفس المستخدم الجاري تعديله.
                    conflict_exists = SchoolMembership.objects.filter(
                        school__in=valid_schools,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    ).exclude(teacher=teacher).exists()
                    if conflict_exists:
                        raise ValidationError("إحدى المدارس المختارة لديها مدير آخر نشط بالفعل. لا يمكن تعيين أكثر من مدير واحد للمدرسة.")

                    # تعطيل أي عضويات إدارة مدارس لم تعد مختارة
                    SchoolMembership.objects.filter(
                        teacher=teacher,
                        role_type=SchoolMembership.RoleType.MANAGER,
                    ).exclude(school__in=valid_schools).update(is_active=False)

                    # تفعيل/إنشاء العضويات المختارة
                    for s in valid_schools:
                        SchoolMembership.objects.update_or_create(
                            school=s,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.MANAGER,
                            defaults={"is_active": True},
                        )
                messages.success(request, "تم تحديث بيانات مدير/مديرة المدرسة بنجاح.")
                return redirect("reports:school_managers_list")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except Exception:
                logger.exception("school_manager_update failed")
                messages.error(request, "تعذّر تحديث بيانات مدير/مديرة المدرسة. تحقّق من البيانات وحاول مرة أخرى.")
        # في حال وجود أخطاء نمرّر selected_ids كما هي
    else:
        existing_ids = SchoolMembership.objects.filter(
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).values_list("school_id", flat=True)
        selected_ids = [str(i) for i in existing_ids]
        form = ManagerCreateForm(instance=manager)

    context = {
        "form": form,
        "schools": schools,
        "selected_ids": [str(i) for i in selected_ids],
        "mode": "edit",
        "manager": manager,
    }
    return render(request, "reports/school_manager_create.html", context)

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def department_delete(request: HttpRequest, code: str) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    if Department is None:
        messages.error(request, "حذف الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: لا يُسمح بحذف قسم يخص مدرسة أخرى.
    # الأقسام العامة (school is NULL) يُسمح بها للسوبر فقط (باستثناء قسم المدير الدائم الذي يمنع حذفه أصلاً).
    try:
        dep_school_id = getattr(obj, "school_id", None)
        if dep_school_id is None:
            if not getattr(request.user, "is_superuser", False):
                messages.error(request, "لا يمكنك حذف قسم عام على مستوى المنصة.")
                return redirect("reports:departments_list")
        elif active_school is not None and dep_school_id != active_school.id:
            messages.error(request, "لا يمكنك حذف قسم يخص مدرسة أخرى.")
            return redirect("reports:departments_list")
    except Exception:
        pass

    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف قسم «{label}».")
    except ProtectedError:
        messages.error(request, f"لا يمكن حذف «{label}» لوجود سجلات مرتبطة به. عطّل القسم أو احذف السجلات المرتبطة أولاً.")
    except Exception:
        logger.exception("department_delete failed")
        messages.error(request, "تعذّر حذف القسم.")
    return redirect("reports:departments_list")

def _dept_m2m_field_name_to_teacher(dep_obj) -> str | None:
    try:
        if dep_obj is None:
            return None
        for f in dep_obj._meta.get_fields():
            if isinstance(f, ManyToManyField) and getattr(f.remote_field, "model", None) is Teacher:
                return f.name
    except Exception:
        logger.exception("Failed to detect forward M2M Department→Teacher")
    return None

def _deptmember_field_names() -> tuple[str | None, str | None]:
    dep_field = tea_field = None
    try:
        if DepartmentMembership is None:
            return (None, None)

        for f in DepartmentMembership._meta.get_fields():
            if isinstance(f, ForeignKey):
                if getattr(f.remote_field, "model", None) is Department and dep_field is None:
                    dep_field = f.name
                elif getattr(f.remote_field, "model", None) is Teacher and tea_field is None:
                    tea_field = f.name
            if dep_field and tea_field:
                break

        if dep_field is None:
            for n in ("department", "dept", "dept_fk"):
                if hasattr(DepartmentMembership, n):
                    dep_field = n
                    break
        if tea_field is None:
            for n in ("teacher", "member", "user", "teacher_fk"):
                if hasattr(DepartmentMembership, n):
                    tea_field = n
                    break
    except Exception:
        logger.exception("Failed to detect DepartmentMembership FKs")

    return (dep_field, tea_field)

def _dept_add_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).add(teacher)
            return True
    except Exception:
        logger.exception("Add via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                DepartmentMembership.objects.get_or_create(**kwargs)
                return True
    except Exception:
        logger.exception("Add via DepartmentMembership failed")

    return False

def _dept_remove_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).remove(teacher)
            return True
    except Exception:
        logger.exception("Remove via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                deleted, _ = DepartmentMembership.objects.filter(**kwargs).delete()
                return deleted > 0
    except Exception:
        logger.exception("Remove via DepartmentMembership failed")

    return False

def _dept_set_member_role(dep, teacher: Teacher, role_type: str) -> bool:
    try:
        if DepartmentMembership is None or Department is None:
            return False

        dep_field, tea_field = _deptmember_field_names()
        if not dep_field or not tea_field:
            return False

        if not hasattr(DepartmentMembership, "role_type"):
            return False

        kwargs = {dep_field: dep, tea_field: teacher}
        obj, created = DepartmentMembership.objects.get_or_create(
            defaults={"role_type": role_type},
            **kwargs,
        )
        if (not created) and getattr(obj, "role_type", None) != role_type:
            obj.role_type = role_type
            obj.save(update_fields=["role_type"])
        return True
    except Exception:
        logger.exception("Failed to set DepartmentMembership role_type")
        return False

def _dept_set_officer(dep, teacher: Teacher) -> bool:
    try:
        if DepartmentMembership is None or Department is None:
            return False

        dep_field, tea_field = _deptmember_field_names()
        if not dep_field or not tea_field:
            return False

        if not hasattr(DepartmentMembership, "role_type"):
            return False

        qs = DepartmentMembership.objects.filter(**{dep_field: dep})
        qs.filter(role_type=DM_OFFICER).exclude(**{tea_field: teacher}).update(role_type=DM_TEACHER)
        return _dept_set_member_role(dep, teacher, DM_OFFICER)
    except Exception:
        logger.exception("Failed to set department officer")
        return False

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_members(request: HttpRequest, code: str | int) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    obj, dept_code, dept_label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not dept_code:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: لا نسمح بإدارة أعضاء قسم تابع لمدرسة أخرى.
    try:
        if obj is not None:
            dep_school_id = getattr(obj, "school_id", None)
            if dep_school_id is None:
                if not getattr(request.user, "is_superuser", False):
                    messages.error(request, "لا يمكنك إدارة قسم عام على مستوى المنصة.")
                    return redirect("reports:departments_list")
            elif active_school is not None and dep_school_id != active_school.id:
                messages.error(request, "لا يمكنك إدارة قسم يخص مدرسة أخرى.")
                return redirect("reports:departments_list")
    except Exception:
        pass

    if request.method == "POST":
        teacher_id = request.POST.get("teacher_id")
        action = (request.POST.get("action") or "").strip()

        allowed_teachers = Teacher.objects.filter(is_active=True)
        if active_school is not None:
            allowed_teachers = allowed_teachers.filter(
                school_memberships__school=active_school,
                school_memberships__is_active=True,
            )
        teacher = allowed_teachers.filter(pk=teacher_id).first()
        if not teacher:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:department_members", code=dept_code)

        if Department is not None and obj:
            try:
                with transaction.atomic():
                    ok = False
                    if action == "add":
                        ok = _dept_set_member_role(obj, teacher, DM_TEACHER) or _dept_add_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تكليف {teacher.name} في قسم «{dept_label}».")
                        else:
                            messages.error(request, "تعذّر إسناد المعلّم — تحقّق من بنية DepartmentMembership.")
                    elif action == "set_officer":
                        ok = _dept_set_officer(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تعيين {teacher.name} مسؤولاً لقسم «{dept_label}». ")
                        else:
                            messages.error(request, "تعذّر تعيين مسؤول القسم — تحقّق من دعم role_type.")
                    elif action == "unset_officer":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name} من القسم.")
                        else:
                            messages.error(request, "تعذّر إلغاء التكليف.")
                    elif action == "remove":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name}.")
                        else:
                            messages.error(request, "تعذّر إلغاء التكليف — تحقق من بنية العلاقات.")
                    else:
                        messages.error(request, "إجراء غير معروف.")
            except Exception:
                logger.exception("department_members mutation failed")
                messages.error(request, "حدث خطأ أثناء حفظ التغييرات.")
        else:
            messages.error(request, "إدارة الأعضاء تتطلب تفعيل موديل Department.")
            return redirect("reports:departments_list")

        return redirect("reports:department_members", code=dept_code)

    members_qs = _members_for_department(dept_code, active_school)

    officers_qs = Teacher.objects.none()
    teachers_qs = Teacher.objects.none()
    assigned_ids_qs = Teacher.objects.none()
    try:
        if DepartmentMembership is not None:
            mem_qs = DepartmentMembership.objects.filter(department__slug__iexact=dept_code)
            if active_school is not None:
                mem_qs = mem_qs.filter(department__school=active_school)
            officer_ids = mem_qs.filter(role_type=DM_OFFICER).values_list("teacher_id", flat=True)
            teacher_ids = mem_qs.filter(role_type=DM_TEACHER).values_list("teacher_id", flat=True)
            assigned_ids = mem_qs.values_list("teacher_id", flat=True)

            officers_qs = Teacher.objects.filter(is_active=True, id__in=officer_ids).distinct().order_by("name")
            teachers_qs = Teacher.objects.filter(is_active=True, id__in=teacher_ids).distinct().order_by("name")
            assigned_ids_qs = Teacher.objects.filter(id__in=assigned_ids)

            if active_school is not None:
                officers_qs = officers_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
                teachers_qs = teachers_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
                assigned_ids_qs = assigned_ids_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
    except Exception:
        logger.exception("Failed to compute officers/teachers memberships")

    all_teachers = Teacher.objects.filter(is_active=True)
    if active_school is not None:
        all_teachers = all_teachers.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
        )
    all_teachers = all_teachers.order_by("name")

    try:
        if hasattr(assigned_ids_qs, "values_list"):
            assigned_ids_list = assigned_ids_qs.values_list("id", flat=True)
            available = all_teachers.exclude(id__in=assigned_ids_list)
        else:
            available = all_teachers
    except Exception:
        available = all_teachers

    return render(
        request,
        "reports/department_members.html",
        {
            "department": obj if obj else {"code": dept_code, "name": dept_label},
            "dept_code": dept_code,
            "dept_label": dept_label,
            "members": members_qs,
            "officers": officers_qs,
            "teachers": teachers_qs,
            "all_teachers": all_teachers,
            "available_teachers": available,
            "has_dept_model": Department is not None,
        },
    )
