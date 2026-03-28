# reports/views/onboarding.py
# -*- coding: utf-8 -*-
"""
Self-service school registration & trial provisioning.

Flow:
1. Principal fills in school details and personal info.
2. A School + Manager account + Trial subscription are created atomically.
3. The user is immediately logged in and redirected to the dashboard.
"""
from __future__ import annotations

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


# ── Trial settings (configurable via env / settings.py) ─────────────
TRIAL_DAYS = int(getattr(settings, "TRIAL_DAYS", 14))
TRIAL_PLAN_NAME = getattr(settings, "TRIAL_PLAN_NAME", "تجربة مجانية")


def _generate_unique_school_code(school_name: str) -> str:
    """Generate a unique slug-like school code from school name."""
    max_length = School._meta.get_field("code").max_length
    base_code = slugify((school_name or "").strip(), allow_unicode=False) or "school"
    base_code = base_code[:max_length]

    candidate = base_code
    suffix_index = 2
    while School.objects.filter(code=candidate).exists():
        suffix = f"-{suffix_index}"
        prefix_max = max_length - len(suffix)
        candidate = f"{base_code[:prefix_max]}{suffix}"
        suffix_index += 1

    return candidate


# ── Registration form ───────────────────────────────────────────────
class SchoolRegistrationForm(forms.Form):
    # School info
    school_name = forms.CharField(
        label="اسم المدرسة", max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "مثال: مدرسة الأمل الابتدائية"}),
    )
    stage = forms.ChoiceField(
        label="المرحلة", choices=School.Stage.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    gender = forms.ChoiceField(
        label="بنين / بنات", choices=School.Gender.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    city = forms.CharField(
        label="المدينة", max_length=120, required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    # Manager info
    manager_name = forms.CharField(
        label="اسم مدير المدرسة", max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    manager_phone = forms.CharField(
        label="رقم الجوال (للدخول)", max_length=20,
        widget=forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "placeholder": "05xxxxxxxx"}),
    )
    password = forms.CharField(
        label="كلمة المرور", min_length=6,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="تأكيد كلمة المرور",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )

    def clean_manager_phone(self):
        phone = self.cleaned_data["manager_phone"].strip()
        if Teacher.objects.filter(phone=phone).exists():
            raise forms.ValidationError("رقم الجوال مسجّل مسبقاً. استخدم صفحة الدخول.")
        return phone

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password_confirm"):
            if cleaned["password"] != cleaned["password_confirm"]:
                self.add_error("password_confirm", "كلمتا المرور غير متطابقتين.")
        return cleaned


# ── View ─────────────────────────────────────────────────────────────
@ratelimit(key="ip", rate="5/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def register_school(request):
    """Self-service school registration with automatic trial subscription."""
    if request.user.is_authenticated:
        return redirect("reports:home")

    if request.method == "POST":
        form = SchoolRegistrationForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # 1. Create school
                    school = None
                    for _ in range(3):
                        generated_school_code = _generate_unique_school_code(form.cleaned_data["school_name"])
                        try:
                            # Savepoint protects the outer transaction if a rare unique race happens.
                            with transaction.atomic():
                                school = School.objects.create(
                                    name=form.cleaned_data["school_name"],
                                    code=generated_school_code,
                                    stage=form.cleaned_data["stage"],
                                    gender=form.cleaned_data["gender"],
                                    city=form.cleaned_data.get("city") or "",
                                    is_active=True,
                                )
                            break
                        except IntegrityError:
                            school = None
                    if school is None:
                        raise IntegrityError("تعذر توليد كود مدرسة فريد.")

                    # 2. Create manager account
                    manager = Teacher.objects.create_user(
                        phone=form.cleaned_data["manager_phone"],
                        name=form.cleaned_data["manager_name"],
                        password=form.cleaned_data["password"],
                    )

                    # 3. Link manager to school
                    SchoolMembership.objects.create(
                        school=school,
                        teacher=manager,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    )

                    # 4. Auto-provision trial subscription
                    plan, _ = SubscriptionPlan.objects.get_or_create(
                        name=TRIAL_PLAN_NAME,
                        defaults={
                            "price": 0,
                            "days_duration": TRIAL_DAYS,
                            "description": f"فترة تجربة مجانية ({TRIAL_DAYS} يوم)",
                            "is_active": True,
                        },
                    )
                    today = timezone.localdate()
                    SchoolSubscription.objects.create(
                        school=school,
                        plan=plan,
                        start_date=today,
                        end_date=today + timedelta(days=TRIAL_DAYS),
                    )

                    # 5. Log the manager in
                    login(request, manager)
                    request.session["active_school_id"] = school.id

                messages.success(
                    request,
                    f"تم تسجيل مدرسة «{school.name}» بنجاح! لديك فترة تجربة مجانية {TRIAL_DAYS} يوم.",
                )
                return redirect("reports:admin_dashboard")

            except Exception as exc:
                messages.error(request, f"حدث خطأ أثناء التسجيل: {exc}")
    else:
        form = SchoolRegistrationForm()

    return render(request, "reports/register_school.html", {"form": form})
