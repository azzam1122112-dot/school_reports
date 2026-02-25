# reports/views/subscriptions.py
# -*- coding: utf-8 -*-
"""Subscription, payment, plan management & footer content pages."""

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url,
    _school_manager_label, _get_active_school,
)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_admin_dashboard(request: HttpRequest) -> HttpResponse:
    """لوحة تحكم خاصة بالمشرف العام لإدارة المنصة بالكامل - تحديث 2026."""
    from django.core.cache import cache
    from django.db.models.functions import TruncMonth
    import json
    
    now = timezone.now()
    
    # البيانات الحرجة (بدون كاش أو كاش قصير جداً)
    pending_payments = Payment.objects.filter(status=Payment.Status.PENDING).count()
    tickets_open = Ticket.objects.filter(status__in=["open", "in_progress"], is_platform=True).count()
    
    # البيانات الإحصائية (كاش 5 دقائق)
    stats_cache_key = "platform_stats_v2"
    stats = cache.get(stats_cache_key)
    
    if not stats:
        reports_count = Report.objects.count()
        teachers_count = Teacher.objects.count()
        
        tickets_total = Ticket.objects.filter(is_platform=True).count()
        tickets_done = Ticket.objects.filter(status="done", is_platform=True).count()
        tickets_rejected = Ticket.objects.filter(status="rejected", is_platform=True).count()

        # تحسين الاستعلامات باستخدام aggregate
        school_stats = School.objects.aggregate(
            total=Count('id'),
            active=Count('id', filter=Q(is_active=True))
        )
        
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
            "reports_count": reports_count,
            "teachers_count": teachers_count,
            "tickets_total": tickets_total,
            "tickets_done": tickets_done,
            "tickets_rejected": tickets_rejected,
            "platform_schools_total": school_stats['total'],
            "platform_schools_active": school_stats['active'],
            "platform_managers_count": platform_managers_count,
            "has_reporttype": has_reporttype,
            "reporttypes_count": reporttypes_count,
        }
        
        try:
            cache.set(stats_cache_key, stats, 300)  # 5 دقائق
        except Exception:
            pass
    
    # بيانات الاشتراكات والمالية (كاش 3 دقائق)
    financial_cache_key = "platform_financial_v2"
    financial = cache.get(financial_cache_key)
    
    if not financial:
        subscriptions_active = SchoolSubscription.objects.filter(is_active=True, end_date__gte=now.date()).count()
        subscriptions_expired = SchoolSubscription.objects.filter(Q(is_active=False) | Q(end_date__lt=now.date())).count()
        subscriptions_expiring_soon = SchoolSubscription.objects.filter(
            is_active=True,
            end_date__gte=now.date(),
            end_date__lte=now.date() + timedelta(days=30)
        ).count()
        
        total_revenue = Payment.objects.filter(status=Payment.Status.APPROVED).aggregate(total=Sum('amount'))['total'] or 0
        
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
            "total_revenue": total_revenue,
            "subscriptions_expiring_list": list(subscriptions_expiring_list),
        }
        
        try:
            cache.set(financial_cache_key, financial, 180)  # 3 دقائق
        except Exception:
            pass
    
    # بيانات الرسوم البيانية (كاش 10 دقائق)
    charts_cache_key = "platform_charts_v2"
    charts = cache.get(charts_cache_key)
    
    if not charts:
        # بيانات الإيرادات الشهرية (آخر 6 أشهر)
        six_months_ago = now - timedelta(days=180)
        revenue_by_month = Payment.objects.filter(
            status=Payment.Status.APPROVED,
            created_at__gte=six_months_ago
        ).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            total=Sum('amount')
        ).order_by('month')
        
        revenue_labels = []
        revenue_data = []
        for item in revenue_by_month:
            month_name = item['month'].strftime('%Y-%m')
            revenue_labels.append(month_name)
            revenue_data.append(float(item['total']))
        
        # بيانات التقارير الأسبوعية (آخر 8 أسابيع)
        eight_weeks_ago = now - timedelta(weeks=8)
        reports_by_week = Report.objects.filter(
            created_at__gte=eight_weeks_ago
        ).annotate(
            week=TruncWeek('created_at')
        ).values('week').annotate(
            count=Count('id')
        ).order_by('week')
        
        # تسمية الأسابيع بالتاريخ بدلاً من رقم الأسبوع (أوضح للمستخدم)
        reports_labels = []
        reports_data = []
        for item in reports_by_week:
            if item['week']:
                # عرض تاريخ بداية الأسبوع (الأحد)
                week_start = item['week'].strftime('%d/%m')
                reports_labels.append(week_start)
                reports_data.append(item['count'])
        
        # توزيع المدارس حسب المرحلة
        schools_by_stage = School.objects.values('stage').annotate(
            count=Count('id')
        ).order_by('stage')
        
        stage_labels = []
        stage_data = []
        stage_colors = []
        color_map = {
            'primary': '#3b82f6',
            'middle': '#10b981', 
            'secondary': '#f59e0b',
            'all': '#8b5cf6'
        }
        
        for item in schools_by_stage:
            stage_name = dict(School.Stage.choices).get(item['stage'], item['stage'])
            stage_labels.append(stage_name)
            stage_data.append(item['count'])
            stage_colors.append(color_map.get(item['stage'], '#6b7280'))
        
        charts = {
            "revenue_labels": json.dumps(revenue_labels),
            "revenue_data": json.dumps(revenue_data),
            "reports_labels": json.dumps(reports_labels),
            "reports_data": json.dumps(reports_data),
            "stage_labels": json.dumps(stage_labels),
            "stage_data": json.dumps(stage_data),
            "stage_colors": json.dumps(stage_colors),
        }
        
        try:
            cache.set(charts_cache_key, charts, 600)  # 10 دقائق
        except Exception:
            pass
    
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
    
    # دمج جميع البيانات
    ctx = {
        **stats,
        **financial,
        **charts,
        "pending_payments": pending_payments,
        "tickets_open": tickets_open,
        "recent_activities": recent_activities,
    }

    return render(request, "reports/platform_admin_dashboard.html", ctx)


# =========================
# صفحات إدارة المنصة المخصصة (بديلة للآدمن)
# =========================

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_audit_logs(request: HttpRequest) -> HttpResponse:
    """عرض سجل العمليات للنظام بالكامل (للمشرف العام)."""
    
    logs_qs = AuditLog.objects.all().select_related("teacher", "school").order_by("-timestamp")

    teacher_id = request.GET.get("teacher")
    action = request.GET.get("action")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if teacher_id:
        logs_qs = logs_qs.filter(teacher_id=teacher_id)
    if action:
        logs_qs = logs_qs.filter(action=action)
    if start_date:
        logs_qs = logs_qs.filter(timestamp__date__gte=start_date)
    if end_date:
        logs_qs = logs_qs.filter(timestamp__date__lte=end_date)

    paginator = Paginator(logs_qs, 50)
    page = request.GET.get("page")
    logs = paginator.get_page(page)

    ctx = {
        "logs": logs,
        "actions": AuditLog.Action.choices,
        "is_platform": True,
        "q_teacher": teacher_id,
        "q_action": action,
        "q_start": start_date,
        "q_end": end_date,
    }
    return render(request, "reports/audit_logs.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscriptions_list(request: HttpRequest) -> HttpResponse:
    today = timezone.localdate()
    status = (request.GET.get("status") or "all").strip().lower()
    plan_id = (request.GET.get("plan") or "").strip()
    q = (request.GET.get("q") or "").strip()

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

    for sub in subscriptions:
        try:
            pref = getattr(sub, "_prefetched_active_payments", []) or []
            sub.has_payment_for_period = any(
                (getattr(p, "payment_date", None) is not None and p.payment_date >= sub.start_date)
                for p in pref
            )
        except Exception:
            sub.has_payment_for_period = False

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

    plans = SubscriptionPlan.objects.all().order_by("price", "name")

    ctx = {
        "subscriptions": subscriptions,
        "status": status,
        "plans": plans,
        "plan_id": plan_id,
        "q": q,
        "stats_total": stats.get("total") or 0,
        "stats_active": stats.get("active") or 0,
        "stats_cancelled": stats.get("cancelled") or 0,
        "stats_expired": stats.get("expired") or 0,
        "results_count": subscriptions.count(),
    }

    return render(request, "reports/platform_subscriptions.html", ctx)


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
    plans = SubscriptionPlan.objects.all().order_by('price')
    return render(request, "reports/platform_plans.html", {"plans": plans})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payments_list(request: HttpRequest) -> HttpResponse:
    status = (request.GET.get("status") or "active").strip().lower()

    base_qs = Payment.objects.select_related('school').order_by('-created_at')

    # ✅ افتراضيًا: لا نعرض (cancelled) ضمن المالية.
    # ملاحظة: الاسترجاعات = عمليات مقبولة بمبلغ سالب.
    if status == "refunds":
        payments = base_qs.filter(status=Payment.Status.APPROVED, amount__lt=0)
    elif status == "cancelled":
        payments = base_qs.filter(status=Payment.Status.CANCELLED)
    elif status == "all":
        payments = base_qs
    else:
        status = "active"
        payments = base_qs.exclude(status=Payment.Status.CANCELLED)
    
    # حساب الإحصائيات لعرضها في الكروت العلوية
    stats = payments.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status=Payment.Status.PENDING)),
        approved=Count('id', filter=Q(status=Payment.Status.APPROVED)),
        rejected=Count('id', filter=Q(status=Payment.Status.REJECTED)),
        cancelled=Count('id', filter=Q(status=Payment.Status.CANCELLED)),
        refunds=Count('id', filter=Q(status=Payment.Status.APPROVED, amount__lt=0)),
    )

    ctx = {
        "payments": payments,
        "status": status,
        "payments_total": stats['total'] or 0,
        "payments_pending": stats['pending'] or 0,
        "payments_approved": stats['approved'] or 0,
        "payments_rejected": stats['rejected'] or 0,
        "payments_cancelled": stats['cancelled'] or 0,
        "payments_refunds": stats['refunds'] or 0,
    }
    return render(request, "reports/platform_payments.html", ctx)

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payment_detail(request: HttpRequest, pk: int) -> HttpResponse:
    payment = get_object_or_404(
        Payment.objects.select_related("school", "subscription", "requested_plan"),
        pk=pk,
    )
    
    if request.method == "POST":
        prev_status = payment.status
        new_status = request.POST.get("status")
        notes = request.POST.get("notes")
        
        if new_status in Payment.Status.values:
            payment.status = new_status
        
        if notes is not None:
            payment.notes = notes

        with transaction.atomic():
            payment.save()

            # ✅ عند اعتماد الدفع لأول مرة: حدّث/جدّد اشتراك المدرسة.
            # ملاحظة: تم إلغاء تغيير الباقة من النظام؛ عند وجود اشتراك قائم نقوم بالتجديد فقط.
            if prev_status != Payment.Status.APPROVED and payment.status == Payment.Status.APPROVED:
                plan_to_apply = payment.requested_plan
                subscription = getattr(payment.school, "subscription", None)

                today = timezone.localdate()

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
                        messages.warning(
                            request,
                            "تم اعتماد الدفع، لكن لا توجد باقة مطلوبة لتفعيل الاشتراك تلقائياً.",
                        )
                else:
                    subscription.is_active = True

                    # ✅ تجديد بنفس الباقة فقط (بدون تغيير الباقة)
                    days = int(getattr(subscription.plan, "days_duration", 0) or 0)
                    subscription.start_date = today
                    subscription.end_date = today if days <= 0 else today + timedelta(days=days - 1)
                    subscription.save(update_fields=["start_date", "end_date", "is_active", "updated_at"])

                if subscription is not None and payment.subscription_id != subscription.id:
                    payment.subscription = subscription
                    payment.save(update_fields=["subscription"])

        messages.success(request, "تم تحديث حالة الدفع بنجاح.")
        return redirect("reports:platform_payment_detail", pk=pk)

    return render(request, "reports/platform_payment_detail.html", {"payment": payment})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_tickets_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()

    # تذاكر الدعم الفني فقط (platform tickets)
    tickets = (
        Ticket.objects.filter(is_platform=True)
        .select_related("creator", "school")
        .order_by("-created_at")
    )

    if status_filter and status_filter != "all":
        tickets = tickets.filter(status=status_filter)

    if query:
        tickets = tickets.filter(
            Q(school__name__icontains=query) |
            Q(school__code__icontains=query) |
            Q(title__icontains=query) |
            Q(id__icontains=query)
        )

    return render(request, "reports/platform_tickets.html", {
        "tickets": tickets,
        "search_query": query,
        "current_status": status_filter,
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
    
    # تظهر آخر 4 عمليات فقط
    payments = Payment.objects.filter(school=membership.school).order_by('-created_at')[:4]
    
    context = {
        "subscription": subscription,
        "school": membership.school,
        # ✅ أظهر كل الخطط (حتى لو غير نشطة) حتى لا تبدو "مفقودة".
        # سيتم تعطيل غير النشطة في القالب.
        "plans": SubscriptionPlan.objects.all().order_by("days_duration", "price"),
        "payments": payments,
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
    
    context = {
        "school": membership.school,
        "payments": payments,
    }
    return render(request, 'reports/subscription_history.html', context)

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
        receipt = request.FILES.get('receipt_image')
        notes = request.POST.get('notes')
        plan_id = request.POST.get('plan_id')

        requested_plan = None
        
        # 1. محاولة أخذ الباقة من اختيار المستخدم
        if plan_id:
            try:
                requested_plan = SubscriptionPlan.objects.get(pk=plan_id)
            except SubscriptionPlan.DoesNotExist:
                pass
        
        # 2. إذا لم يختر، نأخذ الباقة الحالية
        if not requested_plan and subscription:
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

        if not receipt:
            messages.error(request, "يرجى إرفاق صورة الإيصال.")
            return redirect('reports:my_subscription')

        Payment.objects.create(
            school=membership.school,
            subscription=subscription,
            requested_plan=requested_plan,
            amount=amount,
            receipt_image=receipt,
            notes=notes,
            created_by=request.user
        )
        
        msg = f"""
        <div style="text-align: center; line-height: 1.6;">
            <p style="margin-bottom: 0.5rem; font-weight: 700; font-size: 1.1rem;">تم استلام طلبك بنجاح ✅</p>
            <p style="margin-bottom: 0.5rem;">جاري مراجعة الإيصال والتحقق منه، وسيتم تفعيل الباقة التالية فور الاعتماد:</p>
            <div style="background: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.3); padding: 0.75rem 1rem; border-radius: 12px; display: inline-block; margin-top: 0.5rem; color: #fff;">
                <div style="font-weight: 800; font-size: 1.1rem; margin-bottom: 0.25rem;">{requested_plan.name}</div>
                <div style="font-size: 0.9rem;">
                    السعر: {requested_plan.price} ريال &bull; المدة: {requested_plan.days_duration} يوم
                </div>
            </div>
        </div>
        """
        messages.success(request, mark_safe(msg))
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
