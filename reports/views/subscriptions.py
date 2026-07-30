# reports/views/subscriptions.py
# -*- coding: utf-8 -*-
"""Subscription, payment, plan management & footer content pages."""

from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url,
    _school_manager_label, _get_active_school,
    _clean_query_value, _clean_query_params, _parse_date_safe,
)

ARCHIVE_ADDON_ANNUAL_PRICE = Decimal("399.00")
ARCHIVE_ADDON_INCLUDED_STORAGE_GB = 50
ARCHIVE_STORAGE_BLOCK_GB = 50
ARCHIVE_STORAGE_BLOCK_PRICE = Decimal("149.00")


def _archive_pricing():
    try:
        settings_obj = PlatformSettings.get_solo()
    except Exception:
        settings_obj = None

    return {
        "addon_price": Decimal(getattr(settings_obj, "archive_addon_annual_price", ARCHIVE_ADDON_ANNUAL_PRICE) or ARCHIVE_ADDON_ANNUAL_PRICE),
        "included_storage_gb": int(getattr(settings_obj, "archive_included_storage_gb", ARCHIVE_ADDON_INCLUDED_STORAGE_GB) or ARCHIVE_ADDON_INCLUDED_STORAGE_GB),
        "storage_block_gb": int(getattr(settings_obj, "archive_storage_block_gb", ARCHIVE_STORAGE_BLOCK_GB) or ARCHIVE_STORAGE_BLOCK_GB),
        "storage_block_price": Decimal(getattr(settings_obj, "archive_storage_block_price", ARCHIVE_STORAGE_BLOCK_PRICE) or ARCHIVE_STORAGE_BLOCK_PRICE),
    }


def _ensure_default_archive_storage_option(settings_obj: PlatformSettings) -> None:
    if ArchiveStorageOption.objects.exists():
        return
    ArchiveStorageOption.objects.create(
        storage_gb=int(getattr(settings_obj, "archive_storage_block_gb", ARCHIVE_STORAGE_BLOCK_GB) or ARCHIVE_STORAGE_BLOCK_GB),
        price=Decimal(getattr(settings_obj, "archive_storage_block_price", ARCHIVE_STORAGE_BLOCK_PRICE) or ARCHIVE_STORAGE_BLOCK_PRICE),
        sort_order=10,
        is_active=True,
    )


def _archive_storage_options(active_only: bool = True):
    try:
        _ensure_default_archive_storage_option(PlatformSettings.get_solo())
    except Exception:
        pass
    qs = ArchiveStorageOption.objects.all().order_by("sort_order", "storage_gb", "id")
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs)


def _renewal_plan_catalog(current_plan_id: int | None = None) -> list[dict]:
    """Return customer-facing renewal plans grouped by school capacity.

    Trial and inactive plans must never be offered as renewal choices.  The
    catalogue keeps every published paid duration visible inside its capacity
    group so the school can compare all available choices without opening a
    collapsed control first.
    """
    plans = list(
        SubscriptionPlan.objects.filter(
            is_active=True,
            price__gt=0,
        ).order_by(
            "max_teachers",
            "days_duration",
            "price",
            "id",
        )
    )
    groups: dict[int, dict] = {}

    for plan in plans:
        capacity = int(plan.max_teachers or 0)
        group = groups.setdefault(
            capacity,
            {
                "capacity": capacity,
                "capacity_label": (
                    "عدد مستخدمين غير محدود"
                    if capacity <= 0
                    else f"حتى {capacity} مستخدم"
                ),
                "is_recommended": capacity == 50,
                "options": [],
                "features": [],
            },
        )

        days = int(plan.days_duration or 0)
        if 160 <= days <= 210:
            duration_label = "6 أشهر"
            months = 6
        elif 330 <= days <= 400:
            duration_label = "سنة"
            months = 12
        else:
            duration_label = f"{days} يوم"
            months = None

        features = [
            line.strip().lstrip("-*•▪●‣").strip()
            for line in (plan.description or "").replace("\r", "").split("\n")
            if line.strip()
        ][:3]
        if not group["features"] and features:
            group["features"] = features

        monthly_equivalent = None
        if months:
            monthly_equivalent = (plan.price / Decimal(months)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )

        group["options"].append(
            {
                "plan": plan,
                "duration_label": duration_label,
                "monthly_equivalent": monthly_equivalent,
                "is_current": plan.id == current_plan_id,
                "annual_savings": None,
            }
        )

    for group in groups.values():
        semiannual = next(
            (
                option
                for option in group["options"]
                if 160 <= int(option["plan"].days_duration or 0) <= 210
            ),
            None,
        )
        annual = next(
            (
                option
                for option in group["options"]
                if 330 <= int(option["plan"].days_duration or 0) <= 400
            ),
            None,
        )
        if semiannual and annual:
            savings = (semiannual["plan"].price * 2) - annual["plan"].price
            if savings > 0:
                annual["annual_savings"] = savings

    return sorted(
        groups.values(),
        key=lambda group: (
            1 if group["capacity"] <= 0 else 0,
            group["capacity"] if group["capacity"] > 0 else 999999,
        ),
    )


def _payment_purpose_label(payment: Payment) -> str:
    try:
        return payment.get_purpose_display()
    except Exception:
        return "اشتراك المدرسة"


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:platform_login")
@require_http_methods(["GET", "POST"])
def platform_settings(request: HttpRequest) -> HttpResponse:
    """إعدادات المنصة العامة وتسعير الأرشفة."""
    settings_obj = PlatformSettings.get_solo()
    _ensure_default_archive_storage_option(settings_obj)
    form = PlatformSettingsForm(request.POST or None, instance=settings_obj)
    StorageOptionFormSet = forms.modelformset_factory(
        ArchiveStorageOption,
        form=ArchiveStorageOptionForm,
        extra=0,
        can_delete=True,
    )
    storage_options_formset = StorageOptionFormSet(
        request.POST or None,
        queryset=ArchiveStorageOption.objects.all().order_by("sort_order", "storage_gb", "id"),
        prefix="storage_options",
    )

    if request.method == "POST":
        if form.is_valid() and storage_options_formset.is_valid():
            has_active_option = False
            for option_form in storage_options_formset.forms:
                if not getattr(option_form, "cleaned_data", None):
                    continue
                if option_form.cleaned_data.get("DELETE"):
                    continue
                if option_form.cleaned_data.get("storage_gb") and option_form.cleaned_data.get("price") and option_form.cleaned_data.get("is_active"):
                    has_active_option = True

            if not has_active_option:
                messages.error(request, "أضف خيار تخزين واحد مفعّل على الأقل.")
                return render(
                    request,
                    "reports/platform_settings.html",
                    {
                        "form": form,
                        "settings_obj": settings_obj,
                        "storage_options_formset": storage_options_formset,
                    },
                )

            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            storage_options_formset.save()
            try:
                from django.core.cache import cache

                cache.delete("platform_maintenance_state_v1")
            except Exception:
                pass
            messages.success(request, "تم حفظ إعدادات المنصة بنجاح.")
            return redirect("reports:platform_settings")
        messages.error(request, "تعذر حفظ الإعدادات. تحقق من القيم المدخلة.")

    # نظرة عامة على التخزين عبر المنصّة (حقل مخزّن رخيص — بلا أي قراءة شبكية)
    storage_overview = School.objects.aggregate(
        used=Sum("storage_used_bytes"),
        schools=Count("id"),
    )
    platform_storage_used_bytes = int(storage_overview.get("used") or 0)
    schools_count = int(storage_overview.get("schools") or 0)

    return render(
        request,
        "reports/platform_settings.html",
        {
            "form": form,
            "settings_obj": settings_obj,
            "storage_options_formset": storage_options_formset,
            "platform_storage_used_bytes": platform_storage_used_bytes,
            "schools_count": schools_count,
        },
    )


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:platform_login")
def platform_admin_dashboard(request: HttpRequest) -> HttpResponse:
    """لوحة تحكم خاصة بالمشرف العام لإدارة المنصة بالكامل - تحديث 2026."""
    from django.core.cache import cache
    from django.http import JsonResponse
    from django.db.models.functions import TruncMonth
    import json
    
    now = timezone.now()
    force_refresh = (request.GET.get("refresh") or "").strip() == "1"

    period_labels = {
        "all": "الكل",
        "year": "هذا العام",
        "quarter": "هذا الربع",
        "month": "هذا الشهر",
    }

    def _normalize_period(raw: str | None) -> str:
        value = (raw or "all").strip().lower()
        return value if value in period_labels else "all"

    def _period_start(period: str):
        if period == "year":
            return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if period == "quarter":
            quarter_month = ((now.month - 1) // 3) * 3 + 1
            return now.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        if period == "month":
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return None
    
    # البيانات الحرجة (بدون كاش أو كاش قصير جداً)
    # ملاحظة: عدّاد التذاكر المفتوحة يأتي من payload الفترة (kpis.tickets_open) أدناه،
    # لذا لا نحسبه هنا تفاديًا لاستعلام مهدور.
    pending_payments = Payment.objects.filter(status=Payment.Status.PENDING).count()

    # البيانات الإحصائية (كاش 5 دقائق)
    stats_cache_key = "platform_stats_v4"
    stats = None if force_refresh else cache.get(stats_cache_key)
    
    if not stats:
        # ملاحظة: عدّادات التقارير والتذاكر (إجمالي/منجز/مرفوض) تُعرض حسب الفترة المختارة،
        # وتأتي من payload الفترة، لذلك لا نحسب نسخة "كل الوقت" هنا (كانت تُحسب ثم تُتجاوز).
        teachers_count = Teacher.objects.count()

        # تحسين الاستعلامات باستخدام aggregate
        school_stats = School.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True)),
            storage_used=Sum("storage_used_bytes"),
        )

        # ملخص تشغيلي للتخزين دون أي اتصالات شبكية مع R2.
        # الحد الفعلي يأتي من إضافة الأرشيف النشطة، وإلا من الحد المجاني العام.
        platform_settings = PlatformSettings.get_solo()
        free_limit_bytes = max(0, int(getattr(platform_settings, "free_storage_mb", 0) or 0)) * 1024 * 1024
        storage_near_limit_count = 0
        for school in School.objects.only("id", "storage_used_bytes").select_related("archive_addon").iterator():
            limit_bytes = free_limit_bytes
            try:
                addon = school.archive_addon
                if addon.is_active:
                    limit_bytes = max(0, int(addon.storage_limit_gb or 0)) * 1024 * 1024 * 1024
            except SchoolArchiveAddon.DoesNotExist:
                pass
            if limit_bytes > 0 and int(school.storage_used_bytes or 0) >= int(limit_bytes * 0.8):
                storage_near_limit_count += 1
        
        platform_managers_count = (
            Teacher.objects.filter(
                school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
                school_memberships__is_active=True,
            )
            .distinct()
            .count()
        )

        has_reporttype = False
        reporttypes_count = 0
        try:
            from ..models import ReportType  # type: ignore
            has_reporttype = True
            if hasattr(ReportType, "is_active"):
                reporttypes_count = ReportType.objects.filter(is_active=True).count()
            else:
                reporttypes_count = ReportType.objects.count()
        except Exception:
            pass

        stats = {
            "teachers_count": teachers_count,
            "platform_schools_total": school_stats['total'],
            "platform_schools_active": school_stats['active'],
            "platform_storage_used_bytes": int(school_stats["storage_used"] or 0),
            "storage_near_limit_count": storage_near_limit_count,
            "platform_managers_count": platform_managers_count,
            "has_reporttype": has_reporttype,
            "reporttypes_count": reporttypes_count,
        }
        
        try:
            cache.set(stats_cache_key, stats, 300)  # 5 دقائق
        except Exception:
            pass
    
    # بيانات الاشتراكات والمالية (كاش 3 دقائق)
    # ملاحظة: إجمالي الإيرادات يُعرض حسب الفترة المختارة (kpis.total_revenue)، لذا لا نحسب
    # نسخة "كل الوقت" هنا (كانت تُحسب ثم تُتجاوز).
    financial_cache_key = "platform_financial_v3"
    financial = None if force_refresh else cache.get(financial_cache_key)

    if not financial:
        subscriptions_active = SchoolSubscription.objects.filter(is_active=True, end_date__gte=now.date()).count()
        subscriptions_expired = SchoolSubscription.objects.filter(Q(is_active=False) | Q(end_date__lt=now.date())).count()
        subscriptions_expiring_soon = SchoolSubscription.objects.filter(
            is_active=True,
            end_date__gte=now.date(),
            end_date__lte=now.date() + timedelta(days=30)
        ).count()

        # قائمة الاشتراكات المنتهية قريباً (للجدول)
        subscriptions_expiring_list = SchoolSubscription.objects.filter(
            is_active=True,
            end_date__gte=now.date(),
            end_date__lte=now.date() + timedelta(days=30)
        ).select_related('school', 'plan').order_by('end_date')[:10]

        financial = {
            "subscriptions_active": subscriptions_active,
            "subscriptions_expired": subscriptions_expired,
            "subscriptions_expiring_soon": subscriptions_expiring_soon,
            "subscriptions_expiring_list": list(subscriptions_expiring_list),
        }
        
        try:
            cache.set(financial_cache_key, financial, 180)  # 3 دقائق
        except Exception:
            pass

    # ملاحظة: SchoolSubscription.days_remaining خاصية محسوبة (read-only)،
    # والقالب يقرأها مباشرة، فلا حاجة لإسنادها هنا (الإسناد كان يسبب AttributeError).

    # بيانات الرسوم الثابتة (غير مرتبطة بفترة الفلتر) - كاش 10 دقائق
    charts_cache_key = "platform_charts_v2"
    charts = None if force_refresh else cache.get(charts_cache_key)
    
    if not charts:
        # توزيع المدارس حسب المرحلة
        schools_by_stage = School.objects.values('stage').annotate(
            count=Count('id')
        ).order_by('stage')
        
        stage_labels = []
        stage_data = []
        stage_colors = []
        # المفاتيح يجب أن تطابق School.Stage القيم الفعلية: kg / primary / middle / high
        color_map = {
            'kg': '#8b5cf6',       # بنفسجي — رياض أطفال
            'primary': '#3b82f6',  # أزرق — ابتدائي
            'middle': '#10b981',   # أخضر — متوسط
            'high': '#f59e0b',     # كهرماني — ثانوي
        }
        
        for item in schools_by_stage:
            stage_name = dict(School.Stage.choices).get(item['stage'], item['stage'])
            stage_labels.append(stage_name)
            stage_data.append(item['count'])
            stage_colors.append(color_map.get(item['stage'], '#6b7280'))
        
        charts = {
            "stage_labels": json.dumps(stage_labels),
            "stage_data": json.dumps(stage_data),
            "stage_colors": json.dumps(stage_colors),
        }
        
        try:
            cache.set(charts_cache_key, charts, 600)  # 10 دقائق
        except Exception:
            pass

    def _build_period_payload(period: str, *, force: bool = False) -> dict:
        cache_key = f"platform_dashboard_period_payload_v2:{period}"
        cached_payload = None if force else cache.get(cache_key)
        if cached_payload:
            return cached_payload

        start_at = _period_start(period)

        payments_qs = Payment.objects.filter(status=Payment.Status.APPROVED)
        reports_qs = Report.objects.all()
        tickets_qs = Ticket.objects.filter(is_platform=True)
        schools_qs = School.objects.all()

        if start_at is not None:
            payments_qs = payments_qs.filter(payment_date__gte=start_at.date())
            reports_qs = reports_qs.filter(created_at__gte=start_at)
            tickets_qs = tickets_qs.filter(created_at__gte=start_at)
            schools_qs = schools_qs.filter(created_at__gte=start_at)

        total_revenue_period = payments_qs.aggregate(total=Sum("amount"))["total"] or 0
        reports_count_period = reports_qs.count()
        schools_count_period = schools_qs.count()

        tickets_agg = tickets_qs.aggregate(
            total=Count("id"),
            open=Count("id", filter=Q(status__in=["open", "in_progress"])),
            done=Count("id", filter=Q(status="done")),
            rejected=Count("id", filter=Q(status="rejected")),
        )

        revenue_rows = (
            payments_qs
            .annotate(month=TruncMonth("payment_date"))
            .values("month")
            .annotate(total=Sum("amount"))
            .order_by("month")
        )
        reports_rows = (
            reports_qs
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(count=Count("id"))
            .order_by("month")
        )

        revenue_labels: list[str] = []
        revenue_data: list[float] = []
        for row in revenue_rows:
            month_value = row.get("month")
            if month_value is None:
                continue
            revenue_labels.append(month_value.strftime("%Y-%m"))
            revenue_data.append(float(row.get("total") or 0))

        reports_labels: list[str] = []
        reports_data: list[int] = []
        for row in reports_rows:
            month_value = row.get("month")
            if month_value is None:
                continue
            reports_labels.append(month_value.strftime("%Y-%m"))
            reports_data.append(int(row.get("count") or 0))

        payload = {
            "period": period,
            "period_label": period_labels.get(period, "الكل"),
            "generated_at": timezone.localtime(now).strftime("%Y-%m-%d %H:%M"),
            "kpis": {
                "schools_total": int(stats.get("platform_schools_total", 0)),
                "schools_active": int(stats.get("platform_schools_active", 0)),
                "schools_created_in_period": int(schools_count_period),
                "subscriptions_active": int(financial.get("subscriptions_active", 0)),
                "storage_used_bytes": int(stats.get("platform_storage_used_bytes", 0)),
                "storage_near_limit": int(stats.get("storage_near_limit_count", 0)),
                "total_revenue": float(total_revenue_period),
                "reports_count": int(reports_count_period),
                "tickets_total": int(tickets_agg.get("total") or 0),
                "tickets_open": int(tickets_agg.get("open") or 0),
                "tickets_done": int(tickets_agg.get("done") or 0),
                "tickets_rejected": int(tickets_agg.get("rejected") or 0),
            },
            "operations": {
                "pending_payments": int(pending_payments),
                "subscriptions_expiring_soon": int(financial.get("subscriptions_expiring_soon", 0)),
            },
            "charts": {
                "revenue": {
                    "labels": revenue_labels,
                    "data": revenue_data,
                },
                "reports": {
                    "labels": reports_labels,
                    "data": reports_data,
                },
            },
        }

        try:
            cache.set(cache_key, payload, 120)
        except Exception:
            pass

        return payload
    
    # آخر الأنشطة (بدون كاش)
    recent_activities = []
    try:
        recent_payments = Payment.objects.filter(
            status=Payment.Status.APPROVED
        ).select_related('school').order_by('-updated_at')[:5]
        
        for payment in recent_payments:
            recent_activities.append({
                'type': 'payment',
                'icon': 'fa-check-circle',
                'color': 'emerald',
                'title': 'تمت الموافقة على دفعة',
                'description': f"{payment.school.name if payment.school else 'مدرسة'} - {payment.amount} ر.س",
                'time': payment.updated_at,
            })
        
        recent_subscriptions = SchoolSubscription.objects.filter(
            is_active=True
        ).select_related('school', 'plan').order_by('-created_at')[:3]
        
        for sub in recent_subscriptions:
            recent_activities.append({
                'type': 'subscription',
                'icon': 'fa-star',
                'color': 'indigo',
                'title': 'اشتراك جديد',
                'description': f"{sub.school.name} - {sub.plan.name}",
                'time': sub.created_at,
            })
        
        # ترتيب حسب الوقت
        recent_activities.sort(key=lambda x: x['time'], reverse=True)
        recent_activities = recent_activities[:8]
    except Exception:
        pass
    
    selected_period = _normalize_period(request.GET.get("period"))
    period_payload = _build_period_payload(selected_period, force=force_refresh)

    wants_json = (
        request.GET.get("format") == "json"
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("accept") or "")
    )
    if wants_json:
        return JsonResponse(period_payload, json_dumps_params={"ensure_ascii": False})

    # دمج جميع البيانات
    ctx = {
        **stats,
        **financial,
        **charts,
        "pending_payments": pending_payments,
        "tickets_open": int(period_payload["kpis"]["tickets_open"]),
        "recent_activities": recent_activities,
        "initial_period": selected_period,
        "dashboard_period_payload": json.dumps(period_payload, ensure_ascii=False),
        "total_revenue": period_payload["kpis"]["total_revenue"],
        "reports_count": period_payload["kpis"]["reports_count"],
        "tickets_total": period_payload["kpis"]["tickets_total"],
        "tickets_done": period_payload["kpis"]["tickets_done"],
        "tickets_rejected": period_payload["kpis"]["tickets_rejected"],
        "revenue_labels": json.dumps(period_payload["charts"]["revenue"]["labels"], ensure_ascii=False),
        "revenue_data": json.dumps(period_payload["charts"]["revenue"]["data"]),
        "reports_labels": json.dumps(period_payload["charts"]["reports"]["labels"], ensure_ascii=False),
        "reports_data": json.dumps(period_payload["charts"]["reports"]["data"]),
    }

    return render(request, "reports/platform_admin_dashboard.html", ctx)


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:platform_login")
@require_http_methods(["GET"])
def platform_admin_dashboard_data(request: HttpRequest) -> HttpResponse:
    """JSON data endpoint for the platform dashboard."""
    query = request.GET.copy()
    query["format"] = "json"
    request.GET = query
    request.META["HTTP_ACCEPT"] = "application/json"
    request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
    return platform_admin_dashboard(request)


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:platform_login")
@require_http_methods(["GET"])
def platform_admin_dashboard_search(request: HttpRequest) -> HttpResponse:
    """Lightweight global search for the platform admin dashboard."""
    query = (request.GET.get("q") or "").strip()
    results: list[dict[str, str]] = []

    if len(query) < 2:
        return JsonResponse({"results": results}, json_dumps_params={"ensure_ascii": False})
    query_params = urlencode({"q": query})

    school_qs = (
        School.objects.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(city__icontains=query)
            | Q(phone__icontains=query)
        )
        .only("id", "name", "code", "city")
        .order_by("name")[:5]
    )
    for school in school_qs:
        subtitle_bits = [bit for bit in (school.code, school.city) if bit]
        results.append({
            "title": school.name,
            "subtitle": " · ".join(subtitle_bits) or "مدرسة",
            "type": "مدرسة",
            "icon": "fa-school",
            "href": f"{reverse('reports:schools_admin_list')}?{query_params}",
        })

    # نبحث في مدراء المدارس فقط لأنهم الفئة التي يديرها مشرف المنصة وتظهر فعلاً
    # في صفحة الوجهة (school_managers_list). هذا يضمن أن نتيجة البحث قابلة للوصول.
    manager_qs = (
        Teacher.objects.filter(
            Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(national_id__icontains=query),
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .distinct()
        .only("id", "name", "phone")
        .order_by("name")[:5]
    )
    for teacher in manager_qs:
        results.append({
            "title": teacher.name,
            "subtitle": teacher.phone or "مدير مدرسة",
            "type": "مدير مدرسة",
            "icon": "fa-user-tie",
            "href": f"{reverse('reports:school_managers_list')}?{query_params}",
        })

    ticket_qs = (
        Ticket.objects.filter(
            Q(title__icontains=query)
            | Q(body__icontains=query)
            | Q(school__name__icontains=query),
            is_platform=True,
        )
        .select_related("school")
        .only("id", "title", "status", "school__name")
        .order_by("-updated_at")[:5]
    )
    for ticket in ticket_qs:
        results.append({
            "title": f"#{ticket.pk} - {ticket.title}",
            "subtitle": getattr(ticket.school, "name", "") or ticket.get_status_display(),
            "type": "تذكرة",
            "icon": "fa-headset",
            "href": reverse("reports:ticket_detail", kwargs={"pk": ticket.pk}),
        })

    return JsonResponse({"results": results[:12]}, json_dumps_params={"ensure_ascii": False})


# =========================
# صفحات إدارة المنصة المخصصة (بديلة للآدمن)
# =========================

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_audit_logs(request: HttpRequest) -> HttpResponse:
    """عرض سجل العمليات للنظام بالكامل (للمشرف العام)."""
    
    logs_qs = AuditLog.objects.all().select_related("teacher", "school").order_by("-timestamp")

    teacher_id = _clean_query_value(request.GET.get("teacher"))
    action = _clean_query_value(request.GET.get("action"))
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    allowed_actions = {value for value, _label in AuditLog.Action.choices}

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

    params = request.GET.copy()
    if "page" in params:
        params.pop("page")
    for key in list(params.keys()):
        cleaned = _clean_query_value(params.get(key))
        if cleaned:
            params[key] = cleaned
        else:
            params.pop(key)

    teachers = (
        Teacher.objects.filter(
            id__in=AuditLog.objects.values("teacher_id").distinct()
        )
        .only("id", "name", "phone")
        .order_by("name", "id")
    )

    ctx = {
        "logs": logs,
        "teachers": teachers,
        "actions": AuditLog.Action.choices,
        "is_platform": True,
        "q_teacher": teacher_id,
        "q_action": action,
        "q_start": start_date.isoformat() if start_date else "",
        "q_end": end_date.isoformat() if end_date else "",
        "qs": params.urlencode(),
    }
    return render(request, "reports/audit_logs.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscriptions_list(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    status = (request.GET.get("status") or "all").strip().lower()
    plan_id = (request.GET.get("plan") or "").strip()
    q = (request.GET.get("q") or "").strip()
    soon_days = 30
    urgent_days = 7

    base_qs = SchoolSubscription.objects.select_related("school", "plan")

    stats = base_qs.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True, end_date__gte=today)),
        cancelled=Count("id", filter=Q(is_active=False, canceled_at__isnull=False)),
        expired=Count(
            "id",
            filter=Q(
                Q(end_date__lt=today, canceled_at__isnull=True)
                | Q(is_active=False, canceled_at__isnull=True)
            ),
        ),
    )
    money_stats = base_qs.aggregate(
        active_value=Sum("plan__price", filter=Q(is_active=True, end_date__gte=today)),
        expiring_value=Sum(
            "plan__price",
            filter=Q(
                is_active=True,
                end_date__gte=today,
                end_date__lte=today + timedelta(days=soon_days),
            ),
        ),
    )

    payments_period_qs = Payment.objects.filter(
        status=Payment.Status.APPROVED,
        amount__gt=0,
        payment_date__gte=today.replace(day=1),
    )
    payment_stats = payments_period_qs.aggregate(
        collected_this_month=Sum("amount"),
    )

    expiring_soon_count = base_qs.filter(
        is_active=True,
        end_date__gte=today,
        end_date__lte=today + timedelta(days=soon_days),
    ).count()
    urgent_renewals_count = base_qs.filter(
        is_active=True,
        end_date__gte=today,
        end_date__lte=today + timedelta(days=urgent_days),
    ).count()

    subscriptions = base_qs
    if status == "active":
        subscriptions = subscriptions.filter(is_active=True, end_date__gte=today)
    elif status == "cancelled":
        subscriptions = subscriptions.filter(is_active=False, canceled_at__isnull=False)
    elif status == "expired":
        subscriptions = subscriptions.filter(
            Q(end_date__lt=today, canceled_at__isnull=True)
            | Q(is_active=False, canceled_at__isnull=True)
        )

    if plan_id:
        subscriptions = subscriptions.filter(plan_id=plan_id)

    if q:
        subscriptions = subscriptions.filter(school__name__icontains=q)

    subscriptions = subscriptions.order_by("-start_date")

    # ✅ لتفادي N+1: نجلب المدفوعات المرتبطة بكل اشتراك
    subscriptions = subscriptions.prefetch_related(
        Prefetch(
            "payments",
            queryset=Payment.objects.filter(
                status__in=[Payment.Status.PENDING, Payment.Status.APPROVED]
            ).only("id", "subscription_id", "payment_date"),
            to_attr="_prefetched_active_payments",
        )
    )

    # ✅ استرجاعات (refunds): مدفوعات approved بمبالغ سالبة
    subscriptions = subscriptions.prefetch_related(
        Prefetch(
            "payments",
            queryset=Payment.objects.filter(
                status=Payment.Status.APPROVED,
                amount__lt=0,
            ).only("id", "subscription_id", "payment_date", "amount"),
            to_attr="_prefetched_refunds",
        )
    )

    # ✅ حساب بسيط: هل يوجد دفع ضمن فترة الاشتراك الحالية؟
    # نستخدم payment_date >= start_date لتحديد أنه يخص نفس الفترة.
    from decimal import Decimal

    plans = SubscriptionPlan.objects.all().order_by("price", "name")

    paginator = Paginator(subscriptions, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    # تزيين كائنات الصفحة الحالية فقط (بدل كل النتائج)
    collection_gap_count = 0
    for sub in page_obj:
        try:
            pref = getattr(sub, "_prefetched_active_payments", []) or []
            sub.has_payment_for_period = any(
                (getattr(p, "payment_date", None) is not None and p.payment_date >= sub.start_date)
                for p in pref
            )
        except Exception:
            sub.has_payment_for_period = False
        if bool(getattr(sub, "is_active", False)) and not bool(getattr(sub, "is_expired", False)) and not sub.has_payment_for_period and getattr(sub.plan, "price", 0):
            collection_gap_count += 1

        try:
            remaining = int(getattr(sub, "days_remaining", 0))
        except Exception:
            remaining = 0
        sub.commercial_priority = "normal"
        if bool(getattr(sub, "is_cancelled", False)) or bool(getattr(sub, "is_expired", False)):
            sub.commercial_priority = "lost"
        elif not sub.has_payment_for_period and getattr(sub.plan, "price", 0):
            sub.commercial_priority = "collect"
        elif remaining <= urgent_days:
            sub.commercial_priority = "urgent"
        elif remaining <= soon_days:
            sub.commercial_priority = "renew"

        # مبلغ الاسترجاع لهذه الفترة (مجموع القيم السالبة كقيمة موجبة)
        try:
            refunds = getattr(sub, "_prefetched_refunds", []) or []
            total = Decimal("0")
            for p in refunds:
                if getattr(p, "payment_date", None) is not None and p.payment_date >= sub.start_date:
                    amt = getattr(p, "amount", None)
                    if amt is not None:
                        total += (-amt)
            sub.refund_amount_for_period = total
        except Exception:
            sub.refund_amount_for_period = Decimal("0")

    ctx = {
        "subscriptions": page_obj,
        "page_obj": page_obj,
        "status": status,
        "plans": plans,
        "plan_id": plan_id,
        "q": q,
        "stats_total": stats.get("total") or 0,
        "stats_active": stats.get("active") or 0,
        "stats_cancelled": stats.get("cancelled") or 0,
        "stats_expired": stats.get("expired") or 0,
        "results_count": paginator.count,
        "active_value": money_stats.get("active_value") or 0,
        "expiring_value": money_stats.get("expiring_value") or 0,
        "collected_this_month": payment_stats.get("collected_this_month") or 0,
        "expiring_soon_count": expiring_soon_count,
        "urgent_renewals_count": urgent_renewals_count,
        "collection_gap_count": collection_gap_count,
        "soon_days": soon_days,
        "urgent_days": urgent_days,
    }

    return render(request, "reports/platform_subscriptions.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscription_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """تفاصيل اشتراك مدرسة مع سجل العمليات المالية المرتبط به."""
    subscription = get_object_or_404(
        SchoolSubscription.objects.select_related("school", "plan"),
        pk=pk,
    )

    payments_qs = (
        Payment.objects.filter(subscription=subscription)
        .select_related("requested_plan", "created_by")
        .order_by("-created_at")
    )

    period_start = getattr(subscription, "start_date", None)
    has_payment_for_period = False
    if period_start is not None:
        has_payment_for_period = payments_qs.filter(
            status__in=[Payment.Status.PENDING, Payment.Status.APPROVED],
            payment_date__gte=period_start,
        ).exists()

    next_url = _safe_next_url(request.GET.get("next"))

    payments_list = list(payments_qs[:41])
    has_more = len(payments_list) > 40
    if has_more:
        payments_list = payments_list[:40]
    payments_count = payments_qs.count() if has_more else len(payments_list)
    approved_total = payments_qs.filter(status=Payment.Status.APPROVED, amount__gt=0).aggregate(total=Sum("amount")).get("total") or 0
    refund_total = payments_qs.filter(status=Payment.Status.APPROVED, amount__lt=0).aggregate(total=Sum("amount")).get("total") or 0
    pending_total = payments_qs.filter(status=Payment.Status.PENDING).aggregate(total=Sum("amount")).get("total") or 0

    plan_price = getattr(getattr(subscription, "plan", None), "price", 0) or 0
    try:
        outstanding_amount = max(plan_price - approved_total, 0)
    except Exception:
        outstanding_amount = 0

    ctx = {
        "subscription": subscription,
        "payments": payments_list,
        "payments_count": payments_count,
        "has_payment_for_period": has_payment_for_period,
        "next_url": next_url,
        "approved_total": approved_total,
        "refund_total": -refund_total,
        "pending_total": pending_total,
        "outstanding_amount": outstanding_amount,
    }
    return render(request, "reports/platform_subscription_detail.html", ctx)


def _record_subscription_payment_if_missing(
    *,
    subscription: SchoolSubscription,
    actor,
    note: str,
    force: bool = False,
) -> bool:
    """تسجيل عملية دفع (approved) لاشتراك مدرسة في حال عدم وجود دفعة للفترة الحالية.

    نستخدم ذلك لحالات "الاشتراك أُضيف/فُعِّل يدويًا" حتى يظهر في صفحة المالية.
    """
    try:
        if not bool(getattr(subscription, "is_active", False)):
            return False

        plan = getattr(subscription, "plan", None)
        price = getattr(plan, "price", None)
        if price is None:
            return False
        try:
            if float(price) <= 0:
                return False
        except Exception:
            pass

        today = timezone.localdate()
        period_start = getattr(subscription, "start_date", None) or today

        # ✅ تحصين: عند التفعيل/التجديد اليدوي (force=True) لا نريد منع التسجيل
        # بسبب وجود دفعات قديمة، لكن نمنع تكرار نفس العملية في نفس اليوم.
        if force:
            dup_qs = Payment.objects.filter(
                subscription=subscription,
                status__in=[Payment.Status.PENDING, Payment.Status.APPROVED],
                created_at__date=today,
                requested_plan=subscription.plan,
                amount=subscription.plan.price,
            )
            if dup_qs.exists():
                return False
        else:
            existing_qs = Payment.objects.filter(
                subscription=subscription,
                status__in=[Payment.Status.PENDING, Payment.Status.APPROVED],
            )
            # نعتمد created_at بدلاً من payment_date لأن payment_date قد تكون "اليوم" دائماً
            # في التسجيلات اليدوية، مما يمنع تسجيل دفعة جديدة عند التجديد في نفس اليوم.
            existing_qs = existing_qs.filter(created_at__date__gte=period_start)
            if existing_qs.exists():
                return False

        Payment.objects.create(
            school=subscription.school,
            subscription=subscription,
            requested_plan=subscription.plan,
            amount=subscription.plan.price,
            receipt_image=None,
            payment_date=today,
            status=Payment.Status.APPROVED,
            notes=(note or "").strip(),
            created_by=actor,
        )
        return True
    except Exception:
        logger.exception("Failed to record manual payment for subscription")
        return False


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def platform_subscription_delete(request: HttpRequest, pk: int) -> HttpResponse:
    subscription = get_object_or_404(SchoolSubscription.objects.select_related("school", "plan"), pk=pk)

    reason = (request.POST.get("reason") or "").strip()
    refund_raw = (request.POST.get("refund_amount") or "").strip()
    if not reason:
        messages.error(request, "سبب الإلغاء مطلوب لإلغاء الاشتراك.")
        next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
        return redirect(next_url or "reports:platform_subscriptions_list")

    try:
        today = timezone.localdate()
        school_name = subscription.school.name

        with transaction.atomic():
            subscription.is_active = False
            subscription.end_date = today
            if getattr(subscription, "canceled_at", None) is None:
                subscription.canceled_at = timezone.now()
            subscription.cancel_reason = reason

            subscription.save(update_fields=["is_active", "end_date", "canceled_at", "cancel_reason", "updated_at"])

            # ✅ سجل مالي/سجل عمليات المدرسة:
            # نُسجل حدث الإلغاء نفسه كعملية (cancelled) حتى يظهر في:
            # - صفحة المالية (ضمن تبويب cancelled)
            # - صفحة "سجل العمليات السابقة" للمدرسة
            # ولا يؤثر على إجمالي الإيرادات (لأنه مبلغ 0 وبحالة cancelled).
            try:
                exists_cancel_event = Payment.objects.filter(
                    subscription=subscription,
                    status=Payment.Status.CANCELLED,
                    payment_date=today,
                    amount=0,
                ).exists()
                if not exists_cancel_event:
                    Payment.objects.create(
                        school=subscription.school,
                        subscription=subscription,
                        requested_plan=subscription.plan,
                        amount=0,
                        receipt_image=None,
                        payment_date=today,
                        status=Payment.Status.CANCELLED,
                        notes=(
                            "تم إلغاء الاشتراك بواسطة إدارة المنصة.\n"
                            f"سبب الإلغاء: {reason}"
                        ),
                        created_by=request.user,
                    )
            except Exception:
                logger.exception("Failed to record subscription cancellation event")

            # ✅ المالية:
            # - عند الإلغاء: نُلغي فقط المدفوعات المعلّقة لهذه الفترة حتى لا يتم اعتمادها لاحقاً بالخطأ.
            # - خيار إضافي: "استرجاع مبلغ" (كامل/جزئي) عبر تسجيل عملية مالية سالبة (approved)
            #   بحيث يظهر الاسترجاع ويخصم من إجمالي المالية.
            try:
                period_start = getattr(subscription, "start_date", None)

                # 1) إلغاء المعلّق فقط
                pending_qs = Payment.objects.filter(
                    subscription=subscription,
                    status=Payment.Status.PENDING,
                )
                if period_start:
                    pending_qs = pending_qs.filter(payment_date__gte=period_start)

                cancel_note = f"تم إلغاء الاشتراك: {reason}"
                for p in pending_qs.only("id", "status", "notes"):
                    p.status = Payment.Status.CANCELLED
                    p.notes = (f"{p.notes}\n" if (p.notes or "").strip() else "") + cancel_note
                    p.save(update_fields=["status", "notes", "updated_at"])

                # 2) استرجاع مبلغ (اختياري)
                if refund_raw:
                    from decimal import Decimal, InvalidOperation
                    from django.db.models import Sum
                    from django.db.models.functions import Coalesce

                    raw = refund_raw.strip().lower()

                    approved_qs = Payment.objects.filter(
                        subscription=subscription,
                        status=Payment.Status.APPROVED,
                    )
                    if period_start:
                        approved_qs = approved_qs.filter(payment_date__gte=period_start)

                    net_paid = approved_qs.aggregate(total=Coalesce(Sum("amount"), Decimal("0"))).get("total")
                    try:
                        net_paid = Decimal(str(net_paid or "0"))
                    except Exception:
                        net_paid = Decimal("0")

                    max_refund = net_paid if net_paid > 0 else Decimal("0")
                    refund_amount = Decimal("0")

                    if raw in {"full", "كامل", "كاملًا", "كاملا", "استرجاع كامل", "استرجاع كاملًا"}:
                        refund_amount = max_refund
                    else:
                        # السماح بأرقام مثل 100 أو 100.50 أو 100,50
                        try:
                            normalized = raw.replace(",", ".")
                            refund_amount = Decimal(normalized)
                        except (InvalidOperation, ValueError):
                            refund_amount = Decimal("0")

                    if refund_amount < 0:
                        refund_amount = Decimal("0")
                    if refund_amount > max_refund:
                        refund_amount = max_refund

                    # منع الاسترجاع المكرر لنفس اليوم/المبلغ (تحصين بسيط)
                    if refund_amount > 0:
                        exists_refund = Payment.objects.filter(
                            subscription=subscription,
                            status=Payment.Status.APPROVED,
                            amount=-refund_amount,
                            payment_date=today,
                        ).exists()

                        if not exists_refund:
                            Payment.objects.create(
                                school=subscription.school,
                                subscription=subscription,
                                requested_plan=subscription.plan,
                                amount=-refund_amount,
                                receipt_image=None,
                                payment_date=today,
                                status=Payment.Status.APPROVED,
                                notes=(
                                    f"استرجاع مبلغ: {refund_amount} ريال.\n"
                                    f"سبب الإلغاء: {reason}"
                                ),
                                created_by=request.user,
                            )
            except Exception:
                logger.exception("Failed to cancel payments for cancelled subscription")

        messages.success(request, f"تم إلغاء اشتراك مدرسة {school_name}.")
    except Exception:
        logger.exception("platform_subscription_delete failed")
        messages.error(request, "حدث خطأ غير متوقع أثناء إلغاء الاشتراك.")

    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:platform_subscriptions_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_plans_list(request: HttpRequest) -> HttpResponse:
    plans = list(
        SubscriptionPlan.objects.all().order_by(
            "-is_active",
            "max_teachers",
            "days_duration",
            "price",
            "id",
        )
    )
    paired_plans = {}

    for plan in plans:
        if plan.price <= 0:
            plan.period_key = "trial"
            plan.period_label = "تجربة مجانية"
            months = None
        elif plan.days_duration >= 300:
            plan.period_key = "annual"
            plan.period_label = "سنة"
            months = 12
        elif plan.days_duration >= 45:
            plan.period_key = "semiannual"
            plan.period_label = "6 أشهر"
            months = 6
        else:
            plan.period_key = "custom"
            plan.period_label = f"{plan.days_duration} يوم"
            months = None

        plan.monthly_equivalent = None
        if months:
            plan.monthly_equivalent = (plan.price / Decimal(months)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )

        plan.feature_lines = [
            line.strip().lstrip("-*•▪●‣").strip()
            for line in (plan.description or "").replace("\r", "").split("\n")
            if line.strip()
        ][:4]
        plan.is_recommended = bool(plan.price > 0 and plan.max_teachers == 50)
        plan.annual_savings = None
        plan.annual_discount_percent = None
        paired_plans[(plan.max_teachers, plan.period_key)] = plan

    for plan in plans:
        if plan.period_key != "annual":
            continue
        semiannual = paired_plans.get((plan.max_teachers, "semiannual"))
        if semiannual is None:
            continue
        comparison_price = semiannual.price * 2
        savings = max(Decimal("0"), comparison_price - plan.price)
        plan.annual_savings = savings
        if comparison_price > 0:
            plan.annual_discount_percent = int(
                ((savings / comparison_price) * 100).quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP,
                )
            )

    active_plans = [plan for plan in plans if plan.is_active]
    paid_plans = [plan for plan in active_plans if plan.price > 0]
    annual_discounts = [
        plan.annual_discount_percent
        for plan in active_plans
        if plan.annual_discount_percent is not None
    ]
    capacities = {
        plan.max_teachers
        for plan in paid_plans
        if plan.max_teachers > 0
    }
    stats = {
        "active_count": len(active_plans),
        "paid_count": len(paid_plans),
        "semiannual_count": sum(plan.period_key == "semiannual" for plan in paid_plans),
        "annual_count": sum(plan.period_key == "annual" for plan in paid_plans),
        "capacity_count": len(capacities),
        "annual_discount_max": max(annual_discounts, default=0),
    }

    return render(
        request,
        "reports/platform_plans.html",
        {
            "plans": plans,
            "stats": stats,
        },
    )


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_archive_addons_list(request: HttpRequest) -> HttpResponse:
    """إدارة ملحق الأرشفة المدفوع كإضافة مستقلة عن الاشتراك."""
    today = timezone.localdate()
    status = (request.GET.get("status") or "all").strip().lower()
    q = _clean_query_value(request.GET.get("q"))

    addons = SchoolArchiveAddon.objects.select_related("school").order_by("school__name", "id")
    if q:
        addons = addons.filter(Q(school__name__icontains=q) | Q(school__code__icontains=q))

    if status == "active":
        addons = addons.filter(is_enabled=True).filter(Q(end_date__isnull=True) | Q(end_date__gte=today), start_date__lte=today)
    elif status == "disabled":
        addons = addons.filter(is_enabled=False)
    elif status == "expired":
        addons = addons.filter(is_enabled=True, end_date__lt=today)

    stats_base = SchoolArchiveAddon.objects.all()
    stats = {
        "total": stats_base.count(),
        "active": stats_base.filter(is_enabled=True).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=today),
            start_date__lte=today,
        ).count(),
        "expired": stats_base.filter(is_enabled=True, end_date__lt=today).count(),
        "disabled": stats_base.filter(is_enabled=False).count(),
        "schools_without_addon": School.objects.filter(archive_addon__isnull=True).count(),
    }

    page_obj = svc_paginate(addons, per_page=30, page=request.GET.get("page", 1))
    for addon in page_obj:
        try:
            sync_school_archive_storage_usage(addon.school)
            addon.refresh_from_db(fields=["storage_used_bytes", "updated_at"])
        except Exception:
            pass
    return render(
        request,
        "reports/platform_archive_addons.html",
        {
            "addons": page_obj,
            "page_obj": page_obj,
            "stats": stats,
            "status": status,
            "q": q,
            "today": today,
            "results_count": addons.count(),
            "qs": _clean_query_params(request.GET),
        },
    )


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_archive_addon_form(request: HttpRequest, pk: Optional[int] = None) -> HttpResponse:
    addon = get_object_or_404(SchoolArchiveAddon.objects.select_related("school"), pk=pk) if pk else None
    form = SchoolArchiveAddonForm(request.POST or None, instance=addon)

    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ ملحق الأرشيف بنجاح.")
            return redirect("reports:platform_archive_addons_list")
        messages.error(request, "تعذّر الحفظ. تحقق من الحقول.")

    return render(
        request,
        "reports/platform_archive_addon_form.html",
        {
            "form": form,
            "addon": addon,
        },
    )


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def platform_archive_addon_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    addon = get_object_or_404(SchoolArchiveAddon, pk=pk)
    addon.is_enabled = not bool(addon.is_enabled)
    addon.save(update_fields=["is_enabled", "updated_at"])
    messages.success(request, "تم تفعيل ملحق الأرشيف." if addon.is_enabled else "تم إيقاف ملحق الأرشيف.")
    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:platform_archive_addons_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payments_list(request: HttpRequest) -> HttpResponse:
    status = (request.GET.get("status") or "active").strip().lower()
    if status not in {"active", "pending", "refunds", "cancelled", "all"}:
        status = "active"
    q = _clean_query_value(request.GET.get("q"))
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))

    base_qs = Payment.objects.select_related(
        "school", "requested_plan", "subscription", "created_by"
    ).order_by("-created_at")

    # نطاق ثابت يحترم البحث والتاريخ فقط (لا يتأثر بتبويب الحالة) — تُحسب عليه
    # المؤشرات المالية وعدّادات التبويبات حتى تبقى ثابتة عند التنقل بين التبويبات.
    scope_qs = base_qs
    if q:
        query_filter = Q(school__name__icontains=q) | Q(school__code__icontains=q) | Q(notes__icontains=q)
        scope_qs = scope_qs.filter(query_filter)
    if start_date is not None:
        scope_qs = scope_qs.filter(payment_date__gte=start_date)
    if end_date is not None:
        scope_qs = scope_qs.filter(payment_date__lte=end_date)

    # جدول العمليات = النطاق + تبويب الحالة.
    # ملاحظة: الاسترجاعات = عمليات مقبولة بمبلغ سالب.
    if status == "pending":
        payments = scope_qs.filter(status=Payment.Status.PENDING)
    elif status == "refunds":
        payments = scope_qs.filter(status=Payment.Status.APPROVED, amount__lt=0)
    elif status == "cancelled":
        payments = scope_qs.filter(status=Payment.Status.CANCELLED)
    elif status == "all":
        payments = scope_qs
    else:
        payments = scope_qs.exclude(status=Payment.Status.CANCELLED)

    # المؤشرات المالية (مستقلة عن تبويب الحالة، ضمن نطاق البحث/التاريخ)
    stats = scope_qs.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Payment.Status.PENDING)),
        approved=Count("id", filter=Q(status=Payment.Status.APPROVED, amount__gt=0)),
        rejected=Count("id", filter=Q(status=Payment.Status.REJECTED)),
        cancelled=Count("id", filter=Q(status=Payment.Status.CANCELLED)),
        refunds=Count("id", filter=Q(status=Payment.Status.APPROVED, amount__lt=0)),
        gross_revenue=Sum("amount", filter=Q(status=Payment.Status.APPROVED, amount__gt=0)),
        refunds_value=Sum("amount", filter=Q(status=Payment.Status.APPROVED, amount__lt=0)),
        pending_value=Sum("amount", filter=Q(status=Payment.Status.PENDING, amount__gt=0)),
    )
    net_revenue = (stats.get("gross_revenue") or 0) + (stats.get("refunds_value") or 0)

    # عدّادات التبويبات (مشتقة من نفس النطاق دون استعلامات إضافية)
    total_count = stats.get("total") or 0
    cancelled_count = stats.get("cancelled") or 0
    tab_counts = {
        "active": total_count - cancelled_count,
        "pending": stats.get("pending") or 0,
        "refunds": stats.get("refunds") or 0,
        "cancelled": cancelled_count,
        "all": total_count,
    }

    paginator = Paginator(payments, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    if "page" in params:
        params.pop("page")
    # معاملات الفلترة بدون status — لتمرير البحث/التاريخ مع روابط التبويبات
    filter_params = request.GET.copy()
    for key in ("page", "status"):
        filter_params.pop(key, None)

    ctx = {
        "payments": page_obj,
        "page_obj": page_obj,
        "status": status,
        "q": q,
        "start_date": start_date.isoformat() if start_date else "",
        "end_date": end_date.isoformat() if end_date else "",
        "qs": params.urlencode(),
        "filter_qs": filter_params.urlencode(),
        "table_count": payments.count(),
        "tab_counts": tab_counts,
        "payments_total": total_count,
        "payments_pending": stats["pending"] or 0,
        "payments_approved": stats["approved"] or 0,
        "payments_rejected": stats["rejected"] or 0,
        "payments_cancelled": cancelled_count,
        "payments_refunds": stats["refunds"] or 0,
        "gross_revenue": stats.get("gross_revenue") or 0,
        "refunds_value": -(stats.get("refunds_value") or 0),
        "pending_value": stats.get("pending_value") or 0,
        "net_revenue": net_revenue,
    }
    return render(request, "reports/platform_payments.html", ctx)

class _ApprovalError(Exception):
    """خطأ يمنع اعتماد عملية دفع (يستوجب التراجع عن المعاملة)."""


# ترتيب تطبيق أثر الاعتماد داخل الطلب الموحّد: الاشتراك ثم الأرشفة ثم المساحة
# (لأن زيادة المساحة تتطلب وجود إضافة أرشفة مفعّلة).
_PURPOSE_APPLY_ORDER = {
    Payment.Purpose.SUBSCRIPTION: 0,
    Payment.Purpose.ARCHIVE_ADDON: 1,
    Payment.Purpose.ARCHIVE_STORAGE: 2,
}


def _apply_payment_effects(payment, today, pricing):
    """يطبّق أثر اعتماد عملية دفع واحدة حسب غرضها.

    يعيد (level, message) حيث level ∈ {"success", "warning"}.
    يرمي ``_ApprovalError`` إذا تعذّر تطبيق الأثر (يستوجب التراجع).
    """
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.effects_applied_at is not None:
        return ("warning", "سبق تطبيق أثر عملية الدفع هذه؛ لم يُكرّر التفعيل أو زيادة المساحة.")

    def applied(level, message):
        payment.effects_applied_at = timezone.now()
        payment.save(update_fields=["effects_applied_at", "updated_at"])
        try:
            from ..utils import create_system_notification

            manager_ids = list(
                SchoolMembership.objects.filter(
                    school=payment.school,
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                ).values_list("teacher_id", flat=True)
            )
            if manager_ids:
                create_system_notification(
                    title="تم اعتماد طلب المدرسة",
                    message=message,
                    school=payment.school,
                    teacher_ids=manager_ids,
                    is_important=True,
                )
        except Exception:
            logger.exception("Failed to notify school managers after payment approval")
        return (level, message)

    purpose = getattr(payment, "purpose", Payment.Purpose.SUBSCRIPTION)

    if purpose == Payment.Purpose.ARCHIVE_ADDON:
        addon, _created = SchoolArchiveAddon.objects.select_for_update().get_or_create(
            school=payment.school,
            defaults={
                "is_enabled": True,
                "start_date": today,
                "end_date": today + timedelta(days=364),
                "storage_limit_gb": pricing["included_storage_gb"],
                "paid_amount": payment.amount,
            },
        )
        if not _created:
            current_end = addon.end_date if addon.end_date and addon.end_date >= today else today
            addon.is_enabled = True
            addon.start_date = addon.start_date or today
            addon.end_date = current_end + timedelta(days=365)
            addon.storage_limit_gb = max(
                int(addon.storage_limit_gb or 0),
                pricing["included_storage_gb"],
            )
            addon.paid_amount = (addon.paid_amount or 0) + payment.amount
            addon.save(
                update_fields=[
                    "is_enabled", "start_date", "end_date",
                    "storage_limit_gb", "paid_amount", "updated_at",
                ]
            )
        return applied("success", "تم تفعيل/تجديد إضافة الأرشفة للمدرسة تلقائياً.")

    if purpose == Payment.Purpose.ARCHIVE_STORAGE:
        try:
            addon = SchoolArchiveAddon.objects.select_for_update().get(school=payment.school)
        except SchoolArchiveAddon.DoesNotExist:
            raise _ApprovalError("لا يمكن اعتماد زيادة التخزين قبل تفعيل إضافة الأرشفة لهذه المدرسة.")

        added_gb = int(payment.archive_storage_gb or 0)
        if added_gb <= 0:
            raise _ApprovalError("طلب زيادة التخزين لا يحتوي على مساحة صالحة.")

        addon.storage_limit_gb = int(addon.storage_limit_gb or 0) + added_gb
        addon.paid_amount = (addon.paid_amount or 0) + payment.amount
        addon.save(update_fields=["storage_limit_gb", "paid_amount", "updated_at"])
        return applied("success", f"تمت زيادة مساحة أرشيف المدرسة بمقدار {added_gb}GB.")

    # ── الاشتراك ──
    plan_to_apply = payment.requested_plan
    subscription = getattr(payment.school, "subscription", None)
    level, msg = "success", "تم تجديد/تفعيل اشتراك المدرسة تلقائياً."

    if subscription is None:
        if plan_to_apply is not None:
            subscription = SchoolSubscription(
                school=payment.school,
                plan=plan_to_apply,
                start_date=today,
                end_date=today,
                is_active=True,
            )
            subscription.save()
        else:
            level, msg = ("warning", "تم اعتماد الدفع، لكن لا توجد باقة مطلوبة لتفعيل الاشتراك تلقائياً.")
    else:
        if plan_to_apply is not None:
            subscription.plan = plan_to_apply
        subscription.is_active = True
        days = int(getattr(subscription.plan, "days_duration", 0) or 0)
        subscription.start_date = today
        subscription.end_date = today if days <= 0 else today + timedelta(days=days - 1)
        subscription.save(update_fields=["plan", "start_date", "end_date", "is_active", "updated_at"])

    if subscription is not None and payment.subscription_id != subscription.id:
        payment.subscription = subscription
        payment.save(update_fields=["subscription"])

    return applied(level, msg)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payment_detail(request: HttpRequest, pk: int) -> HttpResponse:
    payment = get_object_or_404(
        Payment.objects.select_related("school", "subscription", "requested_plan"),
        pk=pk,
    )

    # ── عمليات الطلب الموحّد الشقيقة (نفس batch_ref) ──
    batch_payments = []
    if payment.batch_ref:
        batch_payments = list(
            Payment.objects.filter(school=payment.school, batch_ref=payment.batch_ref)
            .select_related("requested_plan")
            .order_by("id")
        )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        today = timezone.localdate()
        pricing = _archive_pricing()

        # ===== (أ) اعتماد الطلب الموحّد كاملاً بضغطة واحدة =====
        if action == "approve_batch" and payment.batch_ref:
            pending = [p for p in batch_payments if p.status == Payment.Status.PENDING]
            if not pending:
                messages.info(request, "لا توجد عناصر قيد المراجعة في هذا الطلب.")
                return redirect("reports:platform_payment_detail", pk=pk)

            pending.sort(key=lambda p: _PURPOSE_APPLY_ORDER.get(p.purpose, 99))
            try:
                with transaction.atomic():
                    for p in pending:
                        p.status = Payment.Status.APPROVED
                        p.save(update_fields=["status", "updated_at"])
                        _apply_payment_effects(p, today, pricing)
            except _ApprovalError as exc:
                messages.error(request, f"تعذّر اعتماد الطلب: {exc}")
                return redirect("reports:platform_payment_detail", pk=pk)

            messages.success(request, f"تم اعتماد الطلب الموحّد كاملاً ({len(pending)} عنصر) وتفعيل بنوده تلقائياً.")
            return redirect("reports:platform_payment_detail", pk=pk)

        # ===== (ب) تحديث حالة عملية واحدة =====
        prev_status = payment.status
        new_status = request.POST.get("status")
        notes = request.POST.get("notes")

        if new_status in Payment.Status.values:
            payment.status = new_status
        if notes is not None:
            payment.notes = notes

        try:
            with transaction.atomic():
                payment.save()
                if prev_status != Payment.Status.APPROVED and payment.status == Payment.Status.APPROVED:
                    level, msg = _apply_payment_effects(payment, today, pricing)
                    getattr(messages, level)(request, msg)
        except _ApprovalError as exc:
            messages.error(request, str(exc))
            return redirect("reports:platform_payment_detail", pk=pk)

        messages.success(request, "تم تحديث حالة الدفع بنجاح.")
        return redirect("reports:platform_payment_detail", pk=pk)

    batch_total = sum((p.amount for p in batch_payments), Decimal("0")) if batch_payments else None
    batch_has_pending = any(p.status == Payment.Status.PENDING for p in batch_payments)

    return render(
        request,
        "reports/platform_payment_detail.html",
        {
            "payment": payment,
            "batch_payments": batch_payments,
            "batch_total": batch_total,
            "batch_has_pending": batch_has_pending,
        },
    )

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_tickets_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    # تذاكر الدعم الفني فقط (platform tickets) — نطاق ثابت يحترم البحث فقط
    scope = (
        Ticket.objects.filter(is_platform=True)
        .select_related("creator", "school")
        .order_by("-created_at")
    )
    if query:
        scope = scope.filter(
            Q(school__name__icontains=query) |
            Q(school__code__icontains=query) |
            Q(title__icontains=query) |
            Q(id__icontains=query)
        )

    # عدّادات الحالات (ضمن نطاق البحث، مستقلة عن التبويب المختار)
    tab_counts = scope.aggregate(
        all=Count("id"),
        open=Count("id", filter=Q(status=Ticket.Status.OPEN)),
        in_progress=Count("id", filter=Q(status=Ticket.Status.IN_PROGRESS)),
        done=Count("id", filter=Q(status=Ticket.Status.DONE)),
        rejected=Count("id", filter=Q(status=Ticket.Status.REJECTED)),
    )

    tickets = scope
    if status_filter and status_filter != "all":
        tickets = tickets.filter(status=status_filter)

    paginator = Paginator(tickets, 30)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "reports/platform_tickets.html", {
        "tickets": page_obj,
        "page_obj": page_obj,
        "search_query": query,
        "current_status": status_filter,
        "tab_counts": tab_counts,
    })


# =========================
# إدارة الاشتراكات والمالية
# =========================

def subscription_expired(request):
    """صفحة تظهر عند انتهاء الاشتراك.

    نُمرّر معلومات المدرسة + تاريخ انتهاء الاشتراك إن توفّرت لعرضها في الرسالة.
    """
    school = None
    subscription = None
    is_manager = False

    try:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            active_school = _get_active_school(request)
            memberships = (
                SchoolMembership.objects.filter(teacher=user, is_active=True)
                .select_related("school__subscription__plan", "school")
            )
            membership = None
            if active_school:
                membership = memberships.filter(school=active_school).first()
            if membership is None:
                membership = memberships.first()

            if membership is not None:
                school = membership.school
                is_manager = membership.role_type == SchoolMembership.RoleType.MANAGER
                subscription = getattr(school, "subscription", None)

                # لو أصبحت المدرسة النشطة اشتراكها ساري (بعد التبديل مثلاً)،
                # لا معنى لإظهار صفحة الانتهاء.
                try:
                    if subscription is not None and not bool(subscription.is_expired):
                        if getattr(request.user, "is_superuser", False):
                            return redirect("reports:platform_admin_dashboard")
                        if _is_staff(request.user):
                            return redirect("reports:admin_dashboard")
                        return redirect("reports:home")
                except Exception:
                    pass
    except Exception:
        # لا نكسر الصفحة لو كانت هناك مشكلة في العضويات
        school = None
        subscription = None
        is_manager = False

    return render(
        request,
        "reports/subscription_expired.html",
        {"school": school, "subscription": subscription, "is_manager": is_manager},
    )

@login_required(login_url="reports:login")
def my_subscription(request):
    """صفحة عرض تفاصيل الاشتراك لمدير المدرسة"""
    active_school = _get_active_school(request)
    
    # جلب جميع عضويات الإدارة للمستخدم
    memberships = SchoolMembership.objects.filter(
        teacher=request.user, 
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True
    ).select_related('school__subscription__plan')
    
    membership = None
    # محاولة استخدام المدرسة النشطة إذا كان المستخدم مديراً فيها
    if active_school:
        membership = memberships.filter(school=active_school).first()
    
    # إذا لم توجد مدرسة نشطة أو المستخدم ليس مديراً فيها، نأخذ أول مدرسة يديرها
    if not membership:
        membership = memberships.first()
    
    if not membership:
        messages.error(request, f"عفواً، هذه الصفحة مخصصة لـ{_school_manager_label(active_school)} فقط.")
        return redirect('reports:home')

    # ملاحظة: reverse OneToOne (school.subscription) يرفع DoesNotExist إن لم يوجد سجل
    subscription = (
        SchoolSubscription.objects.filter(school=membership.school)
        .select_related("plan")
        .first()
    )

    archive_addon = SchoolArchiveAddon.objects.filter(school=membership.school).first()
    if archive_addon is not None:
        try:
            sync_school_archive_storage_usage(membership.school)
            archive_addon.refresh_from_db(fields=["storage_used_bytes", "updated_at"])
        except Exception:
            pass
    pricing = _archive_pricing()
    pending_archive_addon_payment = Payment.objects.filter(
        school=membership.school,
        purpose=Payment.Purpose.ARCHIVE_ADDON,
        status=Payment.Status.PENDING,
    ).order_by("-created_at").first()
    pending_archive_storage_payment = Payment.objects.filter(
        school=membership.school,
        purpose=Payment.Purpose.ARCHIVE_STORAGE,
        status=Payment.Status.PENDING,
    ).order_by("-created_at").first()

    # تظهر آخر 4 عمليات فقط
    payments = Payment.objects.filter(school=membership.school).order_by('-created_at')[:4]
    renewal_catalog = _renewal_plan_catalog(
        subscription.plan_id if subscription else None
    )
    renewal_plans = [
        option["plan"]
        for group in renewal_catalog
        for option in group["options"]
    ]
    current_plan_ids = {plan.id for plan in renewal_plans}
    default_renewal_plan_id = (
        subscription.plan_id
        if subscription and subscription.plan_id in current_plan_ids
        else (renewal_plans[0].id if renewal_plans else None)
    )
    
    context = {
        "subscription": subscription,
        "school": membership.school,
        "plans": renewal_plans,
        "renewal_catalog": renewal_catalog,
        "default_renewal_plan_id": default_renewal_plan_id,
        "payments": payments,
        "archive_addon": archive_addon,
        "archive_addon_price": pricing["addon_price"],
        "archive_included_storage_gb": pricing["included_storage_gb"],
        "archive_storage_block_gb": pricing["storage_block_gb"],
        "archive_storage_block_price": pricing["storage_block_price"],
        "archive_storage_options": _archive_storage_options(active_only=True),
        "storage_overview": school_storage_overview(membership.school),
        "pending_archive_addon_payment": pending_archive_addon_payment,
        "pending_archive_storage_payment": pending_archive_storage_payment,
        "has_saved_archives": SchoolYearArchive.objects.filter(
            school=membership.school,
            status__in=[
                SchoolYearArchive.Status.READY,
                SchoolYearArchive.Status.PARTIAL,
            ],
        ).exists(),
    }
    return render(request, 'reports/my_subscription.html', context)

def subscription_history(request):
    """عرض سجل العمليات الكامل للاشتراكات"""
    active_school = _get_active_school(request)
    
    # جلب جميع عضويات الإدارة للمستخدم
    memberships = SchoolMembership.objects.filter(
        teacher=request.user, 
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True
    ).select_related('school')
    
    membership = None
    # محاولة استخدام المدرسة النشطة إذا كان المستخدم مديراً فيها
    if active_school:
        membership = memberships.filter(school=active_school).first()
    
    # إذا لم توجد مدرسة نشطة أو المستخدم ليس مديراً فيها، نأخذ أول مدرسة يديرها
    if not membership:
        membership = memberships.first()
    
    if not membership:
        messages.error(request, f"عفواً، هذه الصفحة مخصصة لـ{_school_manager_label(active_school)} فقط.")
        return redirect('reports:home')

    # جلب كامل العمليات
    payments = Payment.objects.filter(school=membership.school).order_by('-created_at')

    paginator = Paginator(payments, 30)
    page_obj = paginator.get_page(request.GET.get("page"))
    
    context = {
        "school": membership.school,
        "payments": page_obj,
        "page_obj": page_obj,
    }
    return render(request, 'reports/subscription_history.html', context)

def _create_unified_payment(request, membership, subscription):
    """ينشئ طلب دفع موحّد: يجمع الاشتراك + إضافة الأرشفة + زيادة المساحة في إيصال واحد.

    لكل عنصر مختار يُنشأ سجل Payment مستقل بنفس صورة الإيصال (ملف واحد مشترك)،
    حتى يبقى منطق الاعتماد الحالي (لكل غرض على حدة) سليمًا دون تغيير.

    قيد مهم: زيادة مساحة التخزين تتطلب وجود إضافة أرشفة مفعّلة مسبقًا، لأن اعتمادها
    يفشل إن لم تكن الإضافة موجودة. لذلك لا نسمح بطلب المساحة ضمن نفس الطلب الذي
    يُفعّل الإضافة لأول مرة.
    """
    import uuid

    school = membership.school
    receipt = request.FILES.get("receipt_image")
    notes = (request.POST.get("notes") or "").strip()
    pricing = _archive_pricing()

    if not receipt:
        messages.error(request, "يرجى إرفاق صورة الإيصال.")
        return redirect("reports:my_subscription")

    include_sub = (request.POST.get("include_subscription") or "") == "1"
    include_addon = (request.POST.get("include_archive_addon") or "") == "1"
    include_storage = (request.POST.get("include_archive_storage") or "") == "1"

    if not (include_sub or include_addon or include_storage):
        messages.error(request, "اختر عنصرًا واحدًا على الأقل للدفع.")
        return redirect("reports:my_subscription")

    archive_addon = SchoolArchiveAddon.objects.filter(school=school).first()
    addon_active = bool(archive_addon and archive_addon.is_active)

    items = []      # كل عنصر: dict يحوي بيانات إنشاء Payment + label
    warnings = []   # عناصر تم تخطّيها مع سبب

    # 1) اشتراك المنصّة
    if include_sub:
        requested_plan = None
        plan_id = request.POST.get("plan_id")
        if plan_id:
            requested_plan = SubscriptionPlan.objects.filter(
                pk=plan_id,
                is_active=True,
                price__gt=0,
            ).first()
            if requested_plan is None:
                messages.error(request, "الباقة المختارة غير متاحة للتجديد.")
                return redirect("reports:my_subscription")
        if (
            not requested_plan
            and subscription
            and subscription.plan.is_active
            and subscription.plan.price > 0
        ):
            requested_plan = subscription.plan
        if not requested_plan:
            messages.error(request, "يرجى اختيار باقة للاشتراك/التجديد.")
            return redirect("reports:my_subscription")
        amount = getattr(requested_plan, "price", 0) or 0
        try:
            if float(amount) <= 0:
                messages.error(request, "لا يمكن إنشاء طلب دفع لباقة مجانية/غير صالحة.")
                return redirect("reports:my_subscription")
        except (TypeError, ValueError):
            messages.error(request, "سعر الباقة غير صالح.")
            return redirect("reports:my_subscription")
        items.append({
            "purpose": Payment.Purpose.SUBSCRIPTION,
            "requested_plan": requested_plan,
            "amount": amount,
            "label": f"اشتراك: {requested_plan.name}",
        })

    # 2) إضافة الأرشفة (تفعيل/تجديد)
    if include_addon:
        if Payment.objects.filter(
            school=school,
            purpose=Payment.Purpose.ARCHIVE_ADDON,
            status=Payment.Status.PENDING,
        ).exists():
            warnings.append("إضافة الأرشفة (يوجد طلب قيد المراجعة)")
        else:
            items.append({
                "purpose": Payment.Purpose.ARCHIVE_ADDON,
                "requested_plan": None,
                "amount": pricing["addon_price"],
                "label": "إضافة الأرشفة السنوية",
            })

    # 3) زيادة مساحة التخزين (تتطلب إضافة أرشفة مفعّلة مسبقًا)
    if include_storage:
        if not addon_active:
            warnings.append("زيادة المساحة (تتاح بعد تفعيل إضافة الأرشفة)")
        elif Payment.objects.filter(
            school=school,
            purpose=Payment.Purpose.ARCHIVE_STORAGE,
            status=Payment.Status.PENDING,
        ).exists():
            warnings.append("زيادة المساحة (يوجد طلب قيد المراجعة)")
        else:
            option = None
            option_id = request.POST.get("archive_storage_option_id")
            if option_id:
                option = ArchiveStorageOption.objects.filter(pk=option_id, is_active=True).first()
            if option is None:
                messages.error(request, "اختر خيار زيادة مساحة صالح.")
                return redirect("reports:my_subscription")
            items.append({
                "purpose": Payment.Purpose.ARCHIVE_STORAGE,
                "requested_plan": None,
                "amount": option.price,
                "archive_storage_gb": int(option.storage_gb or 0),
                "label": f"زيادة مساحة الأرشيف {option.storage_gb}GB",
            })

    if not items:
        if warnings:
            messages.warning(request, "تعذّر إنشاء الطلب: " + " ، ".join(warnings))
        else:
            messages.error(request, "لا توجد عناصر صالحة للدفع.")
        return redirect("reports:my_subscription")

    batch = uuid.uuid4().hex[:8]
    total = sum((Decimal(str(it["amount"])) for it in items), Decimal("0"))
    labels = "، ".join(it["label"] for it in items)
    base_note = f"[طلب موحّد {batch}] {labels} — الإجمالي {total} ريال."
    if notes:
        base_note = f"{base_note}\nملاحظة المدير: {notes}"

    with transaction.atomic():
        shared_name = None
        for it in items:
            payment = Payment(
                school=school,
                subscription=subscription,
                requested_plan=it.get("requested_plan"),
                purpose=it["purpose"],
                amount=it["amount"],
                archive_storage_gb=it.get("archive_storage_gb", 0),
                notes=base_note,
                batch_ref=batch if len(items) > 1 else "",
                created_by=request.user,
            )
            if shared_name is None:
                # نحفظ الملف مرة واحدة ثم نعيد استخدام اسمه لبقية السجلات
                payment.receipt_image = receipt
                payment.save()
                shared_name = payment.receipt_image.name
            else:
                payment.receipt_image.name = shared_name
                payment.save()

    msg = format_html(
        """
        <div style="text-align:center; line-height:1.7;">
            <p style="margin:0 0 .4rem; font-weight:800; font-size:1.1rem;">تم استلام طلبك الموحّد بنجاح ✅</p>
            <p style="margin:0 0 .5rem;">عدد العناصر: {} &bull; الإجمالي: {} ريال</p>
            <p style="margin:0; font-size:.9rem; opacity:.85;">سيتم تفعيل كل عنصر فور اعتماد مدير النظام.</p>
        </div>
        """,
        len(items),
        total,
    )
    messages.success(request, msg)
    if warnings:
        messages.warning(request, "لم تُضف بعض العناصر: " + " ، ".join(warnings))
    return redirect("reports:my_subscription")


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="5/m", method="POST", block=True)
def payment_create(request):
    """صفحة رفع إيصال الدفع"""
    active_school = _get_active_school(request)
    
    memberships = SchoolMembership.objects.filter(
        teacher=request.user, 
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True
    )
    
    membership = None
    if active_school:
        membership = memberships.filter(school=active_school).first()
        
    if not membership:
        membership = memberships.first()
    
    if not membership:
        messages.error(request, f"عفواً، هذه الصفحة مخصصة لـ{_school_manager_label(active_school)} فقط.")
        return redirect('reports:home')

    subscription = (
        SchoolSubscription.objects.filter(school=membership.school)
        .select_related("plan")
        .first()
    )

    if request.method == 'POST':
        # ✅ مسار الدفع الموحّد: تجميع كل العناصر المختارة في إيصال واحد
        if (request.POST.get("unified") or "").strip() == "1":
            return _create_unified_payment(request, membership, subscription)

        receipt = request.FILES.get('receipt_image')
        notes = (request.POST.get('notes') or "").strip()
        payment_kind = (request.POST.get("payment_kind") or Payment.Purpose.SUBSCRIPTION).strip()
        plan_id = request.POST.get('plan_id')
        requested_plan = None
        pricing = _archive_pricing()

        if not receipt:
            messages.error(request, "يرجى إرفاق صورة الإيصال.")
            return redirect('reports:my_subscription')

        if payment_kind == Payment.Purpose.ARCHIVE_ADDON:
            if Payment.objects.filter(
                school=membership.school,
                purpose=Payment.Purpose.ARCHIVE_ADDON,
                status=Payment.Status.PENDING,
            ).exists():
                messages.warning(request, "لديك طلب تفعيل أرشفة قيد المراجعة بالفعل.")
                return redirect('reports:my_subscription')

            amount = pricing["addon_price"]
            request_notes = "طلب تفعيل/تجديد إضافة الأرشفة السنوية."
            if notes:
                request_notes = f"{request_notes}\n{notes}"

            Payment.objects.create(
                school=membership.school,
                subscription=subscription,
                requested_plan=None,
                purpose=Payment.Purpose.ARCHIVE_ADDON,
                amount=amount,
                receipt_image=receipt,
                notes=request_notes,
                created_by=request.user,
            )
            messages.success(request, "تم رفع طلب تفعيل الأرشفة، وسيظهر الأرشيف فور اعتماد مدير النظام.")
            return redirect('reports:my_subscription')

        if payment_kind == Payment.Purpose.ARCHIVE_STORAGE:
            archive_addon = SchoolArchiveAddon.objects.filter(school=membership.school).first()
            if not archive_addon or not archive_addon.is_active:
                messages.error(request, "زيادة مساحة التخزين متاحة بعد تفعيل إضافة الأرشفة فقط.")
                return redirect('reports:my_subscription')

            if Payment.objects.filter(
                school=membership.school,
                purpose=Payment.Purpose.ARCHIVE_STORAGE,
                status=Payment.Status.PENDING,
            ).exists():
                messages.warning(request, "لديك طلب زيادة مساحة قيد المراجعة بالفعل.")
                return redirect('reports:my_subscription')

            option_id = request.POST.get("archive_storage_option_id")
            storage_option = None
            if option_id:
                try:
                    storage_option = ArchiveStorageOption.objects.get(pk=option_id, is_active=True)
                except (ArchiveStorageOption.DoesNotExist, ValueError, TypeError):
                    storage_option = None

            if storage_option is None:
                messages.error(request, "اختر خيار زيادة مساحة صالح.")
                return redirect('reports:my_subscription')

            storage_gb = int(storage_option.storage_gb or 0)
            amount = storage_option.price

            request_notes = f"طلب زيادة مساحة أرشيف بمقدار {storage_gb}GB."
            if notes:
                request_notes = f"{request_notes}\n{notes}"

            Payment.objects.create(
                school=membership.school,
                subscription=subscription,
                requested_plan=None,
                purpose=Payment.Purpose.ARCHIVE_STORAGE,
                archive_storage_gb=storage_gb,
                amount=amount,
                receipt_image=receipt,
                notes=request_notes,
                created_by=request.user,
            )
            messages.success(request, "تم رفع طلب زيادة مساحة الأرشيف، وسيتم تحديث الحد فور الاعتماد.")
            return redirect('reports:my_subscription')

        # 1. محاولة أخذ الباقة من اختيار المستخدم
        if plan_id:
            requested_plan = SubscriptionPlan.objects.filter(
                pk=plan_id,
                is_active=True,
                price__gt=0,
            ).first()
            if requested_plan is None:
                messages.error(request, "الباقة المختارة غير متاحة للتجديد.")
                return redirect("reports:my_subscription")
        
        # 2. إذا لم يختر، نأخذ الباقة الحالية
        if (
            not requested_plan
            and subscription
            and subscription.plan.is_active
            and subscription.plan.price > 0
        ):
            requested_plan = subscription.plan

        # التحقق النهائي
        if not requested_plan:
            messages.error(request, "يرجى اختيار باقة للاشتراك/التجديد.")
            return redirect('reports:my_subscription')

        amount = getattr(requested_plan, "price", None)
        try:
            if amount is None or float(amount) <= 0:
                messages.error(request, "لا يمكن إنشاء طلب دفع لأن الباقة المختارة مجانية/غير صالحة.")
                return redirect('reports:my_subscription')
        except Exception:
            pass

        Payment.objects.create(
            school=membership.school,
            subscription=subscription,
            requested_plan=requested_plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=amount,
            receipt_image=receipt,
            notes=notes,
            created_by=request.user
        )
        
        msg = format_html("""
        <div style="text-align: center; line-height: 1.6;">
            <p style="margin-bottom: 0.5rem; font-weight: 700; font-size: 1.1rem;">تم استلام طلبك بنجاح ✅</p>
            <p style="margin-bottom: 0.5rem;">جاري مراجعة الإيصال والتحقق منه، وسيتم تفعيل الباقة التالية فور الاعتماد:</p>
            <div style="background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); padding: 0.75rem 1rem; border-radius: 12px; display: inline-block; margin-top: 0.5rem; color: #fff;">
                <div style="font-weight: 800; font-size: 1.1rem; margin-bottom: 0.25rem;">{}</div>
                <div style="font-size: 0.9rem;">
                    السعر: {} ريال &bull; المدة: {} يوم
                </div>
            </div>
        </div>
        """, requested_plan.name, requested_plan.price, requested_plan.days_duration)
        messages.success(request, msg)
        return redirect('reports:my_subscription')
            
    return redirect('reports:my_subscription')


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_plan_form(request: HttpRequest, pk: Optional[int] = None) -> HttpResponse:
    """إضافة أو تعديل خطة اشتراك"""
    plan = None
    if pk:
        plan = get_object_or_404(SubscriptionPlan, pk=pk)
    
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ الخطة بنجاح.")
            return redirect("reports:platform_plans_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = SubscriptionPlanForm(instance=plan)
    
    return render(request, "reports/platform_plan_form.html", {"form": form, "plan": plan})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def platform_plan_delete(request: HttpRequest, pk: int) -> HttpResponse:
    plan = get_object_or_404(SubscriptionPlan, pk=pk)

    try:
        plan_name = plan.name
        plan.delete()
        messages.success(request, f"تم حذف الخطة: {plan_name}.")
    except ProtectedError:
        messages.error(request, "لا يمكن حذف هذه الخطة لأنها مرتبطة باشتراكات مدارس حالياً.")
    except Exception:
        logger.exception("platform_plan_delete failed")
        messages.error(request, "حدث خطأ غير متوقع أثناء حذف الخطة.")

    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:platform_plans_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscription_form(request: HttpRequest, pk: Optional[int] = None) -> HttpResponse:
    """إضافة اشتراك مدرسة (تم إلغاء تعديل الباقة/الاشتراك نهائياً)."""
    subscription = None
    # ✅ تم إلغاء التعديل نهائياً: أي محاولة لفتح رابط قديم للتعديل تُرفض.
    if pk is not None:
        raise Http404
    
    if request.method == "POST":
        # ✅ إذا كانت المدرسة لديها اشتراك سابق (ملغي/منتهي) فلا ننشئ سجل جديد (OneToOne)
        # بل نجدد/نفعّل الاشتراك الموجود لتفادي خطأ "المدرسة موجودة مسبقاً".
        school_id_raw = (request.POST.get("school") or "").strip()
        try:
            school_id = int(school_id_raw)
        except Exception:
            school_id = None

        if school_id is not None:
            existing = (
                SchoolSubscription.objects.filter(school_id=school_id)
                .select_related("school", "plan")
                .first()
            )
            if existing is not None:
                # إن كان الاشتراك ملغي/منتهي: نجدد/نفعّل نفس السجل (OneToOne)
                # لكن نسمح بتغيير الباقة حسب اختيار الإدارة (إن لزم).
                if bool(getattr(existing, "is_cancelled", False)) or bool(getattr(existing, "is_expired", False)):
                    from datetime import timedelta

                    today = timezone.localdate()
                    prev_plan_id = getattr(existing, "plan_id", None)
                    form = SchoolSubscriptionForm(request.POST, instance=existing, allow_plan_change=True)
                    if form.is_valid():
                        subscription_obj = form.save(commit=False)

                        # عند التجديد: فعّل وامسح بيانات الإلغاء
                        subscription_obj.is_active = True
                        if getattr(subscription_obj, "canceled_at", None) is not None:
                            subscription_obj.canceled_at = None
                        if (getattr(subscription_obj, "cancel_reason", "") or "").strip():
                            subscription_obj.cancel_reason = ""

                        # إذا لم تتغير الباقة، فاعتبرها تجديداً أيضاً واضبط التواريخ لليوم
                        # (لأن منطق model.save يعيد الحساب فقط عند تغيير plan).
                        if getattr(subscription_obj, "plan_id", None) == prev_plan_id:
                            days = int(getattr(getattr(subscription_obj, "plan", None), "days_duration", 0) or 0)
                            subscription_obj.start_date = today
                            subscription_obj.end_date = today if days <= 0 else today + timedelta(days=days - 1)

                        subscription_obj.save()

                        # تحصين مالي: أي دفعات pending قديمة لا يجب أن تبقى عالقة بعد التجديد.
                        try:
                            Payment.objects.filter(
                                subscription=subscription_obj,
                                status=Payment.Status.PENDING,
                                created_at__date__lt=subscription_obj.start_date,
                            ).update(
                                status=Payment.Status.CANCELLED,
                                notes="تم إلغاء هذه العملية تلقائياً بسبب تجديد/تغيير الاشتراك.",
                            )
                        except Exception:
                            pass

                        _record_subscription_payment_if_missing(
                            subscription=subscription_obj,
                            actor=request.user,
                            note="تم تجديد الاشتراك (مع تحديث الباقة عند الحاجة) وتسجيل الدفعة بواسطة إدارة المنصة.",
                            force=True,
                        )

                        messages.success(
                            request,
                            f"تم تفعيل/تجديد اشتراك مدرسة {subscription_obj.school.name} حتى {subscription_obj.end_date:%Y-%m-%d}.",
                        )
                        return redirect("reports:platform_subscriptions_list")
                    else:
                        messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
                        return render(request, "reports/platform_subscription_add.html", {"form": form})

                messages.info(
                    request,
                    "هذه المدرسة لديها اشتراك قائم بالفعل. استخدم زر (تجديد) من قائمة الاشتراكات.",
                )
                return redirect("reports:platform_subscriptions_list")

        was_existing = bool(subscription and getattr(subscription, "pk", None))
        prev_is_active = bool(getattr(subscription, "is_active", False)) if subscription else False
        form = SchoolSubscriptionForm(request.POST, instance=subscription)
        if form.is_valid():
            subscription_obj = form.save()

            # ✅ المالية:
            # - عند إنشاء اشتراك جديد من لوحة المنصة: نسجّل دفعة (approved) لتظهر في المالية.
            # - عند تعديل اشتراك موجود: لا نسجّل دفعة إلا إذا كان غير نشط ثم تم تفعيله.
            created_payment = False
            try:
                became_active = (not prev_is_active) and bool(getattr(subscription_obj, "is_active", False))
                if (not was_existing) or became_active:
                    created_payment = _record_subscription_payment_if_missing(
                        subscription=subscription_obj,
                        actor=request.user,
                        note="تم تسجيل الدفعة يدويًا بواسطة إدارة المنصة.",
                        force=False,
                    )
            except Exception:
                created_payment = False

            if created_payment:
                messages.success(request, "تم حفظ الاشتراك وتسجيل عملية الدفع بنجاح.")
            else:
                messages.success(request, "تم حفظ الاشتراك بنجاح.")
            return redirect("reports:platform_subscriptions_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = SchoolSubscriptionForm(instance=subscription)

    return render(request, "reports/platform_subscription_add.html", {"form": form})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def platform_subscription_renew(request: HttpRequest, pk: int) -> HttpResponse:
    """تجديد اشتراك مدرسة مباشرةً من اليوم (ميلادي).

    - يضبط start_date = اليوم
    - يضبط end_date = اليوم + (plan.days_duration - 1)
    - يفعّل is_active=True

    هذا المسار مخصص للمشرف العام فقط لتسهيل التجديد من صفحة الاشتراكات.
    """
    subscription = get_object_or_404(SchoolSubscription.objects.select_related("plan", "school"), pk=pk)

    from datetime import timedelta

    today = timezone.localdate()
    subscription.start_date = today
    days = int(getattr(subscription.plan, "days_duration", 0) or 0)
    if days <= 0:
        subscription.end_date = today
    else:
        subscription.end_date = today + timedelta(days=days - 1)

    subscription.is_active = True
    # عند التجديد: امسح بيانات الإلغاء
    if getattr(subscription, "canceled_at", None) is not None:
        subscription.canceled_at = None
    if getattr(subscription, "cancel_reason", ""):
        subscription.cancel_reason = ""
    subscription.save()

    created_payment = _record_subscription_payment_if_missing(
        subscription=subscription,
        actor=request.user,
        note="تم تجديد الاشتراك وتسجيل الدفعة يدويًا بواسطة إدارة المنصة.",
        force=True,
    )
    if created_payment:
        messages.success(
            request,
            f"تم تجديد اشتراك مدرسة {subscription.school.name} حتى {subscription.end_date:%Y-%m-%d}، وتم تسجيل عملية الدفع.",
        )
    else:
        messages.success(request, f"تم تجديد اشتراك مدرسة {subscription.school.name} حتى {subscription.end_date:%Y-%m-%d}.")

    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:platform_subscriptions_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def platform_subscription_record_payment(request: HttpRequest, pk: int) -> HttpResponse:
    """تسجيل دفعة يدوية لاشتراك موجود بدون تغيير تواريخه."""
    subscription = get_object_or_404(SchoolSubscription.objects.select_related("plan", "school"), pk=pk)

    ok = _record_subscription_payment_if_missing(
        subscription=subscription,
        actor=request.user,
        note="تم تسجيل الدفعة يدويًا بواسطة إدارة المنصة.",
    )
    if ok:
        messages.success(request, "تم تسجيل عملية الدفع بنجاح.")
    else:
        messages.info(request, "لا يمكن تسجيل دفعة جديدة (يوجد دفع بالفعل أو الاشتراك غير نشط/مجاني).")

    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:platform_subscriptions_list")


# ===== صفحات المحتوى (Footer Links) =====


def faq(request: HttpRequest) -> HttpResponse:
    """صفحة الأسئلة الشائعة"""
    return render(request, "reports/faq.html")


def privacy_policy(request: HttpRequest) -> HttpResponse:
    """صفحة سياسة الخصوصية"""
    return render(request, "reports/privacy_policy.html")


# =====================================================================
# إدارة السنوات الدراسية (مدير النظام) — مصدر مركزي لخيارات المدارس
# =====================================================================
@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_academic_years(request: HttpRequest) -> HttpResponse:
    """صفحة تحكم مدير النظام في السنوات الدراسية المتاحة للمدارس."""
    import re
    from ..models import AcademicYear, School

    def _norm(v: str) -> str:
        return (v or "").strip().replace("–", "-").replace("—", "-")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "add":
            value = _norm(request.POST.get("value"))
            if not re.match(r"^\d{4}-\d{4}$", value):
                messages.error(request, "صيغة السنة يجب أن تكون مثل 1447-1448")
            else:
                start, end = value.split("-", 1)
                if int(end) != int(start) + 1:
                    messages.error(request, "السنة يجب أن تكون بفارق سنة واحدة، مثل 1447-1448")
                else:
                    _, created = AcademicYear.objects.get_or_create(
                        value=value, defaults={"is_active": True, "order": int(start)}
                    )
                    messages.success(request, "✅ تمت إضافة السنة." if created else "السنة موجودة مسبقًا.")
            return redirect("reports:platform_academic_years")

        if action == "generate":
            # توليد السنوات الثلاث القادمة تلقائيًا اعتمادًا على آخر سنة مسجّلة
            existing = list(AcademicYear.objects.values_list("value", flat=True))
            anchor = 0
            for v in existing:
                try:
                    anchor = max(anchor, int(str(v)[:4]))
                except Exception:
                    pass
            if anchor == 0:
                today = timezone.localdate()
                g = today.year + (today.month - 1) / 12.0
                anchor = int(round((g - 622) * 33.0 / 32.0))
            added = 0
            for st in range(anchor, anchor + 4):
                _, created = AcademicYear.objects.get_or_create(
                    value=f"{st}-{st + 1}", defaults={"is_active": True, "order": st}
                )
                added += 1 if created else 0
            messages.success(request, f"تم توليد {added} سنة جديدة." if added else "لا توجد سنوات جديدة لإضافتها.")
            return redirect("reports:platform_academic_years")

        if action == "toggle":
            obj = AcademicYear.objects.filter(pk=request.POST.get("id")).first()
            if obj:
                obj.is_active = not obj.is_active
                obj.save(update_fields=["is_active"])
                messages.success(request, "تم تحديث حالة السنة.")
            return redirect("reports:platform_academic_years")

        if action == "delete":
            obj = AcademicYear.objects.filter(pk=request.POST.get("id")).first()
            if obj:
                used = School.objects.filter(current_academic_year=obj.value).count()
                if used:
                    messages.error(request, f"لا يمكن حذف «{obj.value}» لأنها السنة الحالية لـ {used} مدرسة. عطّلها بدلًا من الحذف.")
                else:
                    obj.delete()
                    messages.success(request, "🗑️ تم حذف السنة.")
            return redirect("reports:platform_academic_years")

    years = list(AcademicYear.objects.all().order_by("-value"))
    # عدد المدارس التي تعتمد كل سنة كسنة حالية
    usage = {
        row["current_academic_year"]: row["c"]
        for row in School.objects.exclude(current_academic_year="")
        .values("current_academic_year")
        .annotate(c=Count("id"))
    }
    items = [{"obj": y, "schools": usage.get(y.value, 0)} for y in years]
    active_count = sum(1 for y in years if y.is_active)

    return render(
        request,
        "reports/platform_academic_years.html",
        {"items": items, "total": len(years), "active_count": active_count},
    )
