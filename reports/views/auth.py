# reports/views/auth.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from django.db.models import Q

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url, _set_active_school,
    _get_active_school, _user_schools, _is_report_viewer,
)
from ..middleware import (
    clear_force_password_change_flag,
    is_force_password_change_required,
)
from ..permissions import has_legacy_manager_role


def _force_password_change_notice() -> str:
    return (
        "لحماية حسابك وبيانات المدرسة، يلزم تغيير كلمة المرور الحالية الآن "
        "لأنها ما زالت مطابقة لرقم الجوال."
    )


def _landing_duration_label(days: int) -> str:
    days = int(days or 0)
    if days <= 0:
        return "مدة مرنة"
    if days % 365 == 0:
        years = days // 365
        if years == 1:
            return "لمدة سنة"
        if years == 2:
            return "لمدة سنتين"
        if years <= 10:
            return f"لمدة {years} سنوات"
        return f"لمدة {years} سنة"
    if days % 30 == 0:
        months = days // 30
        if months == 1:
            return "لمدة شهر"
        if months == 2:
            return "لمدة شهرين"
        if months <= 10:
            return f"لمدة {months} أشهر"
        return f"لمدة {months} شهر"
    if days == 1:
        return "لمدة يوم"
    if days == 2:
        return "لمدة يومين"
    if days <= 10:
        return f"لمدة {days} أيام"
    return f"لمدة {days} يوم"


def _landing_default_features(is_trial: bool) -> list[str]:
    if is_trial:
        return [
            "تفعيل مباشر من صفحة التسجيل",
            "تجربة حقيقية للتقارير وملفات الإنجاز وروابط المشاركة",
            "بدء سريع قبل اتخاذ قرار التفعيل",
        ]
    return [
        "إدارة التقارير والتذاكر والتعاميم من مكان واحد",
        "ملفات إنجاز للمعلمين مع PDF وشواهد منظمة",
        "روابط مشاركة مؤقتة بصلاحية محددة للتقارير والإنجاز",
    ]


def _landing_parse_features(description: str, is_trial: bool) -> list[str]:
    text = (description or "").replace("\r", "").strip()
    if not text:
        return _landing_default_features(is_trial)

    raw_parts = []
    for line in text.split("\n"):
        cleaned = re.sub(r"^[\s\-\*\u2022\u25aa\u25cf\u2023]+", "", line or "").strip()
        if cleaned:
            raw_parts.append(cleaned)

    if len(raw_parts) <= 1:
        split_parts = [p.strip() for p in re.split(r"[؛\n]+", text) if p.strip()]
        if split_parts:
            raw_parts = split_parts

    unique_parts: list[str] = []
    seen = set()
    for item in raw_parts:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique_parts.append(item.strip())

    if not unique_parts:
        unique_parts = _landing_default_features(is_trial)

    defaults = _landing_default_features(is_trial)
    for fallback in defaults:
        if len(unique_parts) >= 3:
            break
        if fallback not in unique_parts:
            unique_parts.append(fallback)

    return unique_parts[:3]


def _landing_fit_text(capacity: int, is_trial: bool, is_unlimited: bool) -> str:
    if is_trial:
        return "مناسبة لاختبار المنتج داخل المدرسة قبل التوسع"
    if is_unlimited:
        return "مناسبة للتشغيل الموسع مع سعة مستخدمين مرنة"
    if capacity <= 25:
        return "مناسبة للمدارس الصغيرة أو فرق الإدارة المحدودة"
    if capacity <= 50:
        return "الأنسب لغالبية المدارس عند التشغيل الكامل"
    if capacity <= 75:
        return "مناسبة للمدارس الأكبر أو الفرق متعددة الأدوار"
    return "مناسبة للتشغيل الواسع داخل المدرسة"


def _landing_segment_label(users: int) -> str:
    if users <= 25:
        return "فريق صغير"
    if users <= 50:
        return "مدرسة متوسطة"
    if users <= 75:
        return "مدرسة كبيرة"
    return "تشغيل موسع"


def _landing_period_key(days: int, is_trial: bool) -> str | None:
    if is_trial:
        return "trial"
    days = int(days or 0)
    if days >= 300:
        return "1y"
    if days >= 45:
        return "6m"
    return None


def _landing_card_title(capacity: int, is_unlimited: bool) -> str:
    if is_unlimited:
        return "باقة تشغيل موسعة"
    if capacity <= 0:
        return "باقة مخصصة"
    return f"باقة {capacity} مستخدم"


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@never_cache
@cache_control(no_cache=True, must_revalidate=True, no_store=True, max_age=0)
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        if is_force_password_change_required(request):
            return redirect("reports:my_profile")
        # إن كان المستخدم موظّف لوحة (مدير/سوبر أدمن) نوجّهه للوحة المناسبة
        if getattr(request.user, "is_superuser", False):
            return redirect("reports:platform_admin_dashboard")
        if is_platform_admin(request.user):
            return redirect("reports:platform_schools_directory")
        if _is_staff(request.user):
            return redirect("reports:admin_dashboard")
        return redirect("reports:home")

    if request.method == "POST":
        identifier = (
            request.POST.get("phone")
            or request.POST.get("username")
            or request.POST.get("identifier")
            or ""
        ).strip()
        password = request.POST.get("password") or ""

        # يدعم تسجيل الدخول عبر:
        # - رقم الجوال (المعرف الافتراضي USERNAME_FIELD)
        # - رقم الهوية (نبحث عنه ثم نستخدم phone)
        # مع بعض التطبيع الخفيف لأشكال رقم الجوال الشائعة.
        attempts: list[str] = []
        if identifier:
            attempts.append(identifier)
            ident_no_plus = identifier.lstrip("+")
            if ident_no_plus != identifier:
                attempts.append(ident_no_plus)
            if identifier.isdigit() and len(identifier) == 9:
                attempts.append("0" + identifier)
            if ident_no_plus.isdigit() and ident_no_plus.startswith("966") and len(ident_no_plus) >= 12:
                # +9665XXXXXXXX -> 05XXXXXXXX
                attempts.append("0" + ident_no_plus[-9:])

        # إزالة التكرارات مع الحفاظ على الترتيب
        seen: set[str] = set()
        attempts = [a for a in attempts if a and not (a in seen or seen.add(a))]

        user = None
        for phone_candidate in attempts:
            user = authenticate(request, username=phone_candidate, password=password)
            if user is not None:
                break

        if user is None and identifier:
            try:
                potential_by_national = Teacher.objects.filter(national_id=identifier).only("phone").first()
                if potential_by_national is not None and getattr(potential_by_national, "phone", None):
                    user = authenticate(request, username=potential_by_national.phone, password=password)
            except Exception:
                user = None
        if user is not None:
            # ✅ قواعد الاشتراك عند تسجيل الدخول:
            # - السوبر: يتجاوز دائمًا.
            # - مدير المدرسة: يُسمح له بالدخول حتى لو انتهى الاشتراك، لكن يُوجّه لصفحة (انتهاء الاشتراك)
            #   ولا يستطيع استخدام المنصة إلا لصفحات التجديد (يُفرض ذلك عبر SubscriptionMiddleware).
            # - بقية المستخدمين: إن لم توجد أي مدرسة باشتراك ساري → نمنع تسجيل الدخول.

            if not getattr(user, "is_superuser", False):
                try:
                    memberships = (
                        SchoolMembership.objects.filter(teacher=user, is_active=True)
                        .select_related("school")
                        .order_by("id")
                    )

                    # إن لم تكن هناك أي عضوية مدرسة، لا نمنع تسجيل الدخول برسالة اشتراك (لأننا لا نستطيع ربطه بمدرسة).
                    # هذا يحدث أحياناً لحسابات قديمة أو حسابات لم تُربط بعد.
                    if not memberships.exists():
                        login(request, user)
                        force_password_change = is_force_password_change_required(request)
                        messages.warning(request, "تنبيه: حسابك غير مرتبط بمدرسة فعّالة. تواصل مع إدارة النظام لربط الحساب بالمدرسة.")
                        if force_password_change:
                            messages.warning(request, _force_password_change_notice())
                            return redirect("reports:my_profile")
                        next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                        if getattr(user, "is_superuser", False):
                            default_name = "reports:platform_admin_dashboard"
                        elif is_platform_admin(user):
                            default_name = "reports:platform_schools_directory"
                        elif _is_staff(user):
                            default_name = "reports:admin_dashboard"
                        else:
                            default_name = "reports:home"
                        return redirect(next_url or default_name)

                    active_school = None
                    any_active_subscription = False
                    is_any_manager = False
                    manager_school = None
                    first_school_name = None

                    legacy_manager_role = has_legacy_manager_role(user)

                    for m in memberships:
                        if first_school_name is None:
                            first_school_name = getattr(getattr(m, "school", None), "name", None)
                        if m.role_type == SchoolMembership.RoleType.MANAGER:
                            is_any_manager = True
                            if manager_school is None:
                                manager_school = m.school

                        # دعم حسابات مدير قديمة تعتمد على Role(slug='manager') حتى لو role_type مختلف.
                        if not is_any_manager and legacy_manager_role:
                            is_any_manager = True
                            if manager_school is None:
                                manager_school = m.school

                        sub = None
                        try:
                            sub = getattr(m.school, "subscription", None)
                        except Exception:
                            sub = None

                        # عدم وجود اشتراك = منتهي
                        if sub is not None and not bool(sub.is_expired) and bool(getattr(m.school, "is_active", True)):
                            any_active_subscription = True
                            if active_school is None:
                                active_school = m.school

                    if not any_active_subscription:
                        if is_any_manager and manager_school is not None:
                            # المدير يُسمح له بالدخول للتجديد فقط
                            login(request, user)
                            is_force_password_change_required(request)
                            _set_active_school(request, manager_school)
                            return redirect("reports:subscription_expired")

                        school_label = f" ({first_school_name})" if first_school_name else ""
                        messages.error(request, f"عذرًا، اشتراك المدرسة{school_label} منتهي. لا يمكن الدخول حتى يتم تجديد الاشتراك.")
                        return redirect("reports:login")

                    # هناك اشتراك ساري واحد على الأقل → نكمل تسجيل الدخول ونثبت مدرسة نشطة مناسبة
                    login(request, user)
                    if active_school is not None:
                        _set_active_school(request, active_school)
                except Exception:
                    # في حال أي مشكلة في تحقق الاشتراك، لا نكسر تسجيل الدخول (سيتولى Middleware المنع لاحقاً)
                    login(request, user)
            else:
                login(request, user)

            # بعد تسجيل الدخول مباشرةً: اختيار مدرسة افتراضية عند توفر مدرسة واحدة فقط
            try:
                # إن كان للمستخدم مدرسة واحدة فقط ضمن عضوياته نعتبرها المدرسة النشطة
                schools = _user_schools(user)
                if len(schools) == 1:
                    _set_active_school(request, schools[0])
                # أو إن كان مشرفاً عاماً وهناك مدرسة واحدة فقط مفعّلة في النظام
                elif user.is_superuser:
                    qs = School.objects.filter(is_active=True)
                    if qs.count() == 1:
                        s = qs.first()
                        if s is not None:
                            _set_active_school(request, s)
            except Exception:
                pass

            force_password_change = is_force_password_change_required(request)
            if force_password_change:
                messages.warning(request, _force_password_change_notice())
                return redirect("reports:my_profile")

            next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
            # الوجهة الافتراضية حسب الدور
            if getattr(user, "is_superuser", False):
                default_name = "reports:platform_admin_dashboard"
            elif is_platform_admin(user):
                default_name = "reports:platform_schools_directory"
            elif _is_staff(user):
                default_name = "reports:admin_dashboard"
            else:
                default_name = "reports:home"
            return redirect(next_url or default_name)

        # فشل المصادقة: نتحقق هل السبب هو أن الحساب موقوف (is_active=False)
        try:
            from django.db.models import Q

            q = Q()
            if attempts:
                q |= Q(phone__in=attempts)
            if identifier:
                q |= Q(national_id=identifier)

            potential_user = Teacher.objects.filter(q).first() if q else None
            if potential_user is not None and (not potential_user.is_active) and potential_user.check_password(password):
                messages.error(request, "عذرًا، حسابك موقوف. يرجى التواصل مع الإدارة.")
            else:
                messages.error(request, "رقم الجوال/الهوية أو كلمة المرور غير صحيحة")
        except Exception:
            messages.error(request, "رقم الجوال/الهوية أو كلمة المرور غير صحيحة")

    context = {"next": _safe_next_url(request.GET.get("next"))}
    return render(request, "reports/login.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def logout_view(request: HttpRequest) -> HttpResponse:
    _set_active_school(request, None)
    logout(request)
    return redirect("reports:login")


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def my_profile(request: HttpRequest) -> HttpResponse:
    """بروفايل المستخدم الحالي.

    - متاح لكل المستخدمين.
    - حساب (مشرف تقارير - عرض فقط) لا يدخلها عادةً، ويُسمح له بها فقط عند إجباره على تغيير كلمة المرور.
    - يعرض الاسم + المدارس المسندة.
    - يسمح بتغيير رقم الجوال + تغيير كلمة المرور.
    """

    active_school = _get_active_school(request)
    force_password_change = is_force_password_change_required(request)
    if (not force_password_change) and (_is_report_viewer(request.user, active_school) or _is_report_viewer(request.user)):
        messages.error(request, "هذا الحساب للعرض فقط ولا يملك صفحة بروفايل.")
        return redirect("reports:school_reports_readonly")

    memberships = (
        SchoolMembership.objects.filter(teacher=request.user, is_active=True)
        .select_related("school")
        .order_by("school__name", "id")
    )

    phone_form = MyProfilePhoneForm(instance=request.user, prefix="phone")
    pwd_form = MyPasswordChangeForm(request.user, prefix="pwd")

    if request.method == "POST":
        if "update_phone" in request.POST:
            if force_password_change:
                messages.info(request, "لتأمين الحساب أولاً، غيّر كلمة المرور ثم سيصبح تحديث رقم الجوال متاحًا مباشرة.")
                return redirect("reports:my_profile")
            phone_form = MyProfilePhoneForm(request.POST, instance=request.user, prefix="phone")
            if phone_form.is_valid():
                try:
                    phone_form.save()
                    messages.success(request, "تم تحديث رقم الجوال بنجاح.")
                    return redirect("reports:my_profile")
                except IntegrityError:
                    messages.error(request, "تعذر تحديث رقم الجوال (قد يكون مستخدمًا بالفعل).")
        elif "update_password" in request.POST:
            pwd_form = MyPasswordChangeForm(request.user, request.POST, prefix="pwd")
            if pwd_form.is_valid():
                user = pwd_form.save()
                update_session_auth_hash(request, user)
                try:
                    new_session_key = request.session.session_key or ""
                    if new_session_key and getattr(user, "current_session_key", "") != new_session_key:
                        user.current_session_key = new_session_key
                        user.save(update_fields=["current_session_key"])
                except Exception:
                    pass
                clear_force_password_change_flag(request)

                # إرسال إيميل تأكيد تغيير كلمة المرور (في الخلفية)
                try:
                    from ..utils import run_task_safe
                    from ..tasks import send_password_change_email_task
                    run_task_safe(send_password_change_email_task, user.pk)
                except Exception:
                    pass

                messages.success(request, "تم تحديث كلمة المرور بنجاح.")
                return redirect("reports:my_profile")

    ctx = {
        "active_school": active_school,
        "memberships": memberships,
        "phone_form": phone_form,
        "pwd_form": pwd_form,
        "force_password_change": force_password_change,
    }
    return render(request, "reports/my_profile.html", ctx)


@require_http_methods(["GET"])
def platform_landing(request: HttpRequest) -> HttpResponse:
    """الصفحة الرئيسية العامة للمنصة (تعريف + مميزات + زر دخول).

    - المستخدِم المسجّل بالفعل يُعاد توجيهه مباشرةً للواجهة المناسبة.
    - الزر الأساسي يقود إلى شاشة تسجيل الدخول العادية.
    """

    if getattr(request.user, "is_authenticated", False):
        if getattr(request.user, "is_superuser", False):
            return redirect("reports:platform_admin_dashboard")
        if is_platform_admin(request.user):
            return redirect("reports:platform_schools_directory")
        if _is_staff(request.user):
            return redirect("reports:admin_dashboard")
        return redirect("reports:home")

    plans_qs = SubscriptionPlan.objects.filter(is_active=True).order_by("price", "max_teachers", "days_duration", "id")
    source_plans = list(plans_qs)
    trial_days_target = int(getattr(settings, "TRIAL_DAYS", 14) or 14)

    def serialize_plan(plan: SubscriptionPlan, *, is_trial: bool) -> dict[str, Any]:
        raw_price = float(getattr(plan, "price", 0) or 0)
        raw_capacity = int(getattr(plan, "max_teachers", 0) or 0)
        capacity = raw_capacity
        if is_trial and capacity <= 0:
            capacity = 5
        is_unlimited = (raw_capacity <= 0) and (not is_trial)

        description = (getattr(plan, "description", "") or "").strip()
        summary = description.split("\n", 1)[0].strip() if description else ""
        if not summary:
            summary = _landing_fit_text(capacity, is_trial, is_unlimited)

        if abs(raw_price - round(raw_price)) < 0.001:
            price_display = f"{int(round(raw_price)):,}"
            price_int = int(round(raw_price))
        else:
            price_display = f"{raw_price:,.2f}".rstrip("0").rstrip(".")
            price_int = int(round(raw_price))

        if is_unlimited:
            capacity_label = "مستخدمون غير محدودين"
            capacity_hint = 999999
        elif capacity <= 2:
            capacity_label = f"حتى {capacity} مستخدم"
            capacity_hint = capacity
        elif capacity <= 10:
            capacity_label = f"حتى {capacity} مستخدمين"
            capacity_hint = capacity
        else:
            capacity_label = f"حتى {capacity} مستخدم"
            capacity_hint = capacity

        return {
            "id": int(getattr(plan, "id", 0) or 0),
            "source_name": (getattr(plan, "name", "") or "").strip() or "باقة",
            "summary": summary,
            "features": _landing_parse_features(description, is_trial),
            "fit_text": _landing_fit_text(capacity, is_trial, is_unlimited),
            "price_value": raw_price,
            "price_int": price_int,
            "price_display": price_display,
            "duration_days": int(getattr(plan, "days_duration", 0) or 0),
            "duration_label": _landing_duration_label(int(getattr(plan, "days_duration", 0) or 0)),
            "capacity": capacity,
            "capacity_hint": capacity_hint,
            "capacity_label": capacity_label,
            "is_trial": is_trial,
            "is_unlimited": is_unlimited,
            "period_key": _landing_period_key(int(getattr(plan, "days_duration", 0) or 0), is_trial),
            "cta_label": "سجّل المدرسة الآن" if is_trial else "ابدأ بالتجربة ثم فعّل",
        }

    trial_candidates = [plan for plan in source_plans if float(getattr(plan, "price", 0) or 0) <= 0]
    trial_source = None
    if trial_candidates:
        trial_source = min(
            trial_candidates,
            key=lambda p: (
                abs(int(getattr(p, "days_duration", 0) or 0) - trial_days_target),
                0 if int(getattr(p, "max_teachers", 0) or 0) <= 5 else 1,
                int(getattr(p, "days_duration", 0) or 0),
                int(getattr(p, "id", 0) or 0),
            ),
        )

    pricing_trial_plan = serialize_plan(trial_source, is_trial=True) if trial_source is not None else None
    if pricing_trial_plan is not None:
        pricing_trial_plan["name"] = "التجربة المجانية"
        pricing_trial_plan["badge"] = f'{pricing_trial_plan["duration_label"]} تجريبية'
        pricing_trial_plan["cta_secondary_label"] = "لديك حساب بالفعل؟"

    paid_source = [plan for plan in source_plans if float(getattr(plan, "price", 0) or 0) > 0]
    paid_groups: dict[str, dict[str, Any]] = {}
    available_periods = {"6m": False, "1y": False}

    for source_plan in paid_source:
        plan = serialize_plan(source_plan, is_trial=False)
        period_key = plan["period_key"]
        if period_key not in {"6m", "1y"}:
            continue

        available_periods[period_key] = True
        group_key = str(plan["capacity_hint"])
        group = paid_groups.setdefault(
            group_key,
            {
                "capacity_hint": plan["capacity_hint"],
                "capacity_label": plan["capacity_label"],
                "fit_text": plan["fit_text"],
                "is_unlimited": plan["is_unlimited"],
                "plans": {},
            },
        )
        existing = group["plans"].get(period_key)
        target_days = 365 if period_key == "1y" else 180
        if existing is None or (
            abs(plan["duration_days"] - target_days),
            plan["price_value"],
            plan["id"],
        ) < (
            abs(existing["duration_days"] - target_days),
            existing["price_value"],
            existing["id"],
        ):
            group["plans"][period_key] = plan

    pricing_cards: list[dict[str, Any]] = []
    for group in sorted(
        paid_groups.values(),
        key=lambda item: (
            1 if int(item["capacity_hint"]) >= 999999 else 0,
            int(item["capacity_hint"]),
        ),
    ):
        plans_by_period = group["plans"]
        default_plan = plans_by_period.get("6m") or plans_by_period.get("1y")
        if default_plan is None:
            continue

        card = {
            "capacity_hint": group["capacity_hint"],
            "capacity_label": group["capacity_label"],
            "fit_text": group["fit_text"],
            "name": _landing_card_title(int(default_plan["capacity"]), bool(default_plan["is_unlimited"])),
            "cta_label": "ابدأ بالتجربة ثم فعّل",
            "period_6m": plans_by_period.get("6m"),
            "period_1y": plans_by_period.get("1y"),
            "periods": {
                "6m": plans_by_period.get("6m"),
                "1y": plans_by_period.get("1y"),
            },
            "is_featured": False,
            "is_recommended": False,
            "badge": "",
        }
        pricing_cards.append(card)

    initial_period = "6m" if available_periods["6m"] else "1y"
    paid_view = [card for card in pricing_cards if card["periods"].get(initial_period) is not None]
    if not paid_view:
        paid_view = pricing_cards[:]

    recommended_plan = None
    if paid_view:
        recommended_plan = min(
            paid_view,
            key=lambda card: (
                abs((card["capacity_hint"] if card["capacity_hint"] < 999999 else 75) - 50),
                float((card["periods"].get(initial_period) or card["periods"].get("1y") or card["periods"].get("6m"))["price_value"]),
                int(card["capacity_hint"]),
            ),
        )

    if recommended_plan is not None:
        recommended_plan["is_featured"] = True
        recommended_plan["is_recommended"] = True

    cheapest_paid = None
    if paid_view:
        cheapest_paid = min(
            paid_view,
            key=lambda card: (
                float((card["periods"].get(initial_period) or card["periods"].get("1y") or card["periods"].get("6m"))["price_value"]),
                int(card["capacity_hint"]),
            ),
        )

    for card in pricing_cards:
        if recommended_plan is not None and card is recommended_plan:
            card["badge"] = "الأكثر طلباً"
        elif cheapest_paid is not None and card is cheapest_paid:
            card["badge"] = "اقتصادية"

    known_caps = [int(card["capacity_hint"]) for card in pricing_cards if int(card["capacity_hint"]) < 999999]
    known_max = max(known_caps) if known_caps else 75
    for card in pricing_cards:
        if int(card["capacity_hint"]) >= 999999:
            card["capacity_hint"] = known_max + 25

    slider_min = 5
    slider_max = max([int(card["capacity_hint"]) for card in pricing_cards], default=100)
    slider_max = max(slider_max, 25)
    slider_step = 5
    initial_users = int(recommended_plan["capacity_hint"]) if recommended_plan is not None else 50
    initial_users = max(slider_min, min(initial_users, slider_max))

    mark_values = sorted({int(card["capacity_hint"]) for card in pricing_cards})
    if not mark_values:
        mark_values = [25, 50, 75, 100]

    if len(mark_values) > 4:
        index_set = {0, len(mark_values) // 3, (2 * len(mark_values)) // 3, len(mark_values) - 1}
        mark_values = [mark_values[i] for i in sorted(index_set)]

    active_mark = min(mark_values, key=lambda v: abs(v - initial_users))
    advisor_marks = [
        {
            "value": v,
            "label": _landing_segment_label(v),
            "active": v == active_mark,
        }
        for v in mark_values
    ]

    ctx = {
        "pricing_trial_plan": pricing_trial_plan,
        "pricing_cards": pricing_cards,
        "pricing_plans": pricing_cards,
        "pricing_recommended": recommended_plan,
        "pricing_initial_period": initial_period,
        "pricing_periods": [
            {"key": "6m", "label": "6 أشهر", "available": available_periods["6m"], "active": initial_period == "6m"},
            {"key": "1y", "label": "سنة", "available": available_periods["1y"], "active": initial_period == "1y"},
        ],
        "pricing_slider": {
            "min": slider_min,
            "max": slider_max,
            "step": slider_step,
            "initial": active_mark,
        },
        "advisor_marks": advisor_marks,
    }

    return render(request, "reports/landing.html", ctx)
