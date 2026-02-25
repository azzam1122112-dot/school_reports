# reports/views/auth.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db.models import Q

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url, _set_active_school,
    _get_active_school, _user_schools, _is_report_viewer,
)


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@never_cache
@cache_control(no_cache=True, must_revalidate=True, no_store=True, max_age=0)
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
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
                        messages.warning(request, "تنبيه: حسابك غير مرتبط بمدرسة فعّالة. تواصل مع إدارة النظام لربط الحساب بالمدرسة.")
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

                    role_slug = getattr(getattr(user, "role", None), "slug", None)

                    for m in memberships:
                        if first_school_name is None:
                            first_school_name = getattr(getattr(m, "school", None), "name", None)
                        if m.role_type == SchoolMembership.RoleType.MANAGER:
                            is_any_manager = True
                            if manager_school is None:
                                manager_school = m.school

                        # دعم حسابات مدير قديمة تعتمد على Role(slug='manager') حتى لو role_type مختلف.
                        if not is_any_manager and role_slug == "manager":
                            is_any_manager = True
                            if manager_school is None:
                                manager_school = m.school

                        sub = None
                        try:
                            sub = getattr(m.school, 'subscription', None)
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

    - متاح لكل المستخدمين ما عدا (مشرف تقارير - عرض فقط).
    - يعرض الاسم + المدارس المسندة.
    - يسمح بتغيير رقم الجوال + تغيير كلمة المرور.
    """

    active_school = _get_active_school(request)
    if _is_report_viewer(request.user, active_school) or _is_report_viewer(request.user):
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
                messages.success(request, "تم تحديث كلمة المرور بنجاح.")
                return redirect("reports:my_profile")

    ctx = {
        "active_school": active_school,
        "memberships": memberships,
        "phone_form": phone_form,
        "pwd_form": pwd_form,
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

    return render(request, "reports/landing.html")
