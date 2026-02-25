# reports/views/teachers.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _safe_next_url, _model_has_field,
    _get_active_school, _user_manager_schools,
)

# =========================
# إدارة المعلّمين (مدير فقط)
# =========================
@login_required(login_url="reports:login")
@role_required({"manager"})  # إن كنت تبغى السماح للسوبر دائمًا، خلي role_required يتجاوز للسوبر أو أضف دور admin
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)

    # ✅ اجبار اختيار مدرسة لغير السوبر (أوضح وأأمن)
    if not request.user.is_superuser:
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")

        if active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    term = (request.GET.get("q") or "").strip()

    qs = Teacher.objects.select_related("role").order_by("-id")

    # ✅ عزل حسب المدرسة (نُظهر المعلمين + مشرفي التقارير المرتبطين بالمدرسة)
    if active_school is not None:
        qs = qs.filter(
            school_memberships__school=active_school,
            school_memberships__role_type__in=[
                SchoolMembership.RoleType.TEACHER,
                SchoolMembership.RoleType.REPORT_VIEWER,
            ],
        ).distinct()

    # ✅ بحث
    if term:
        qs = qs.filter(
            Q(name__icontains=term) |
            Q(phone__icontains=term) |
            Q(national_id__icontains=term)
        )

    # ✅ annotate: role_slug/label
    qs = qs.annotate(
        role_slug=F("role__slug"),
        role_label=F("role__name"),
    )

    # ✅ تمييز مشرف التقارير داخل المدرسة النشطة
    if active_school is not None:
        try:
            title_sq = (
                SchoolMembership.objects.filter(
                    school=active_school,
                    teacher=OuterRef("pk"),
                    role_type=SchoolMembership.RoleType.TEACHER,
                )
                .values("job_title")[:1]
            )
            viewer_m = SchoolMembership.objects.filter(
                school=active_school,
                teacher=OuterRef("pk"),
                role_type=SchoolMembership.RoleType.REPORT_VIEWER,
            )
            qs = qs.annotate(
                is_report_viewer=Exists(viewer_m),
                school_job_title=Subquery(title_sq),
            )
        except Exception:
            pass

    # ✅ اسم القسم من Department حسب slug مع تقييد المدرسة (إن كان Department فيه FK school)
    if Department is not None:
        dept_qs = Department.objects.filter(slug=OuterRef("role__slug"))
        if active_school is not None and _model_has_field(Department, "school"):
            dept_qs = dept_qs.filter(Q(school=active_school) | Q(school__isnull=True))
        dept_name_sq = dept_qs.values("name")[:1]
        qs = qs.annotate(role_dept_name=Subquery(dept_name_sq))

    # ✅ منع N+1: Prefetch عضويات الأقسام مرة واحدة وبحقول أقل
    if DepartmentMembership is not None:
        dm_qs = (
            DepartmentMembership.objects
            .select_related("department")
            .only("id", "teacher_id", "role_type", "department__id", "department__name", "department__slug")
            .order_by("department__name")
        )
        if active_school is not None and _model_has_field(Department, "school"):
            dm_qs = dm_qs.filter(Q(department__school=active_school) | Q(department__school__isnull=True))

        qs = qs.prefetch_related(Prefetch("dept_memberships", queryset=dm_qs))

    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "reports/manage_teachers.html", {"teachers_page": page, "term": term})

@login_required(login_url="reports:login")
@role_required({"manager"})
@ratelimit(key="user", rate="5/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def bulk_import_teachers(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    # Defense-in-depth: تأكد أن المدير يملك صلاحية على المدرسة النشطة
    try:
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    except Exception:
        pass

    # الاستيراد يُنشئ عضويات TEACHER؛ نتحقق من وجود اشتراك فعّال لتجنب ValidationError العام
    sub = getattr(active_school, "subscription", None)
    try:
        if sub is None or bool(getattr(sub, "is_expired", True)):
            messages.error(request, "لا يوجد اشتراك فعّال لهذه المدرسة.")
            return redirect("reports:my_subscription")
    except Exception:
        messages.error(request, "لا يوجد اشتراك فعّال لهذه المدرسة.")
        return redirect("reports:my_subscription")

    if request.method == "POST":
        excel_file = request.FILES.get("excel_file")
        if not excel_file:
            messages.error(request, "الرجاء اختيار ملف Excel.")
            return render(request, "reports/bulk_import_teachers.html")

        # تحقق بسيط من الامتداد لتقليل أخطاء المستخدم
        try:
            fname = (getattr(excel_file, "name", "") or "").lower()
            if not fname.endswith(".xlsx"):
                messages.error(request, "الملف غير صالح. الرجاء اختيار ملف بصيغة .xlsx")
                return render(request, "reports/bulk_import_teachers.html")
        except Exception:
            pass

        try:
            import re
            from django.core.exceptions import ValidationError

            def _norm_str(v) -> str:
                return (str(v).strip() if v is not None else "").strip()

            def _normalize_phone(v) -> str:
                if v is None:
                    return ""
                # openpyxl يعيد int/float للأرقام
                try:
                    if isinstance(v, bool):
                        return ""
                    if isinstance(v, int):
                        s = str(v)
                    elif isinstance(v, float):
                        s = str(int(v)) if float(v).is_integer() else str(v)
                    else:
                        s = str(v)
                except Exception:
                    s = str(v)
                s = s.strip()
                # إزالة المسافات والرموز الشائعة (نحتفظ بالأرقام فقط)
                digits = re.sub(r"\D+", "", s)
                if not digits:
                    return s

                # تطبيع أرقام الجوال الشائعة (السعودية)
                # - 9665XXXXXXXX  -> 05XXXXXXXX
                # - 5XXXXXXXX     -> 05XXXXXXXX
                try:
                    if digits.startswith("966"):
                        digits = digits[3:]
                    if digits.startswith("5") and len(digits) == 9:
                        digits = "0" + digits
                    if digits.startswith("5") and len(digits) == 10:
                        digits = "0" + digits[-9:]
                except Exception:
                    pass
                return digits

            def _normalize_national_id(v) -> str:
                s = _norm_str(v)
                if not s:
                    return ""
                digits = re.sub(r"\D+", "", s)
                return digits or s

            wb = openpyxl.load_workbook(excel_file, read_only=True, data_only=True)
            sheet = wb.active

            def _norm_header(v) -> str:
                s = _norm_str(v).lower()
                s = re.sub(r"\s+", "", s)
                s = re.sub(r"[\-_/\\]+", "", s)
                return s

            header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None) or ()
            header_norm = [_norm_header(h) for h in (header_row or ())]

            def _find_col_idx(candidates: tuple[str, ...]) -> int | None:
                for i, h in enumerate(header_norm):
                    if not h:
                        continue
                    for c in candidates:
                        if c and c in h:
                            return i
                return None

            # نحدد الأعمدة حسب العناوين لتفادي ملفات فيها أعمدة فارغة/غير متجاورة
            name_idx = _find_col_idx(("الاسمالكامل", "اسم", "الاسم"))
            phone_idx = _find_col_idx(("رقمالجوال", "الجوال", "رقمالهاتف", "الهاتف"))
            nat_idx = _find_col_idx(("رقمالهوية", "الهوية", "السجلالمدني", "رقمالسجل"))

            # توقع الأعمدة: الاسم، رقم الجوال، رقم الهوية (اختياري)
            # الصف الأول عناوين
            parsed_rows: list[tuple[int, str, str, str | None]] = []
            phones_in_file: set[str] = set()
            nat_ids_in_file: set[str] = set()

            max_rows_guard = 2000
            for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                if len(parsed_rows) >= max_rows_guard:
                    messages.error(request, f"الملف يحتوي على عدد كبير من الصفوف (>{max_rows_guard}). الرجاء تقسيم الملف.")
                    return render(request, "reports/bulk_import_teachers.html")

                row = row or ()
                # إن استطعنا تحديد الأعمدة من العناوين: نقرأ حسب الفهارس
                if name_idx is not None or phone_idx is not None or nat_idx is not None:
                    name = row[name_idx] if name_idx is not None and name_idx < len(row) else None
                    phone = row[phone_idx] if phone_idx is not None and phone_idx < len(row) else None
                    national_id = row[nat_idx] if nat_idx is not None and nat_idx < len(row) else None
                else:
                    # fallback للملفات التي بلا عناوين واضحة
                    name, phone, national_id = (row + (None, None, None))[:3]

                name_s = _norm_str(name)
                phone_s = _normalize_phone(phone)
                nat_s = _normalize_national_id(national_id) or None

                if nat_s:
                    nat_ids_in_file.add(nat_s)

                if not name_s or not phone_s:
                    # نؤجل الأخطاء إلى مرحلة الرسائل حتى لا نقطع المعالجة مبكرًا
                    parsed_rows.append((row_idx, name_s, phone_s, nat_s))
                    continue

                if phone_s in phones_in_file:
                    parsed_rows.append((row_idx, name_s, phone_s, nat_s))
                    continue
                phones_in_file.add(phone_s)
                parsed_rows.append((row_idx, name_s, phone_s, nat_s))

            if not parsed_rows:
                messages.error(request, "الملف فارغ أو لا يحتوي على بيانات.")
                return render(request, "reports/bulk_import_teachers.html")

            # التحقق من حد الباقة (نحسب فقط العضويات الجديدة الفعلية)
            max_teachers = int(getattr(getattr(sub, "plan", None), "max_teachers", 0) or 0)
            current_count = SchoolMembership.objects.filter(
                school=active_school,
                role_type=SchoolMembership.RoleType.TEACHER,
            ).count()

            phones_unique = {p for p in phones_in_file if p}
            existing_phones_in_school: set[str] = set()
            if phones_unique:
                existing_phones_in_school = set(
                    SchoolMembership.objects.filter(
                        school=active_school,
                        role_type=SchoolMembership.RoleType.TEACHER,
                        teacher__phone__in=phones_unique,
                    ).values_list("teacher__phone", flat=True)
                )

            expected_new = len([p for p in phones_unique if p not in existing_phones_in_school])
            if max_teachers > 0 and (current_count + expected_new) > max_teachers:
                remaining = max_teachers - current_count
                messages.error(request, f"لا يمكن استيراد {expected_new} معلّم جديد. الحد المتبقي في باقتك هو {remaining}.")
                return render(request, "reports/bulk_import_teachers.html")

            created_count = 0
            updated_count = 0
            reactivated_count = 0
            errors: list[str] = []
            seen_phone_rows: set[str] = set()

            # ملاحظة مهمة: أي IntegrityError داخل atomic قد يكسر المعاملة في Postgres.
            # لذلك نستخدم savepoint لكل صف حتى نستمر في الصفوف التالية.
            for row_idx, name_s, phone_s, nat_s in parsed_rows:
                with transaction.atomic():
                    if not name_s or not phone_s:
                        errors.append(f"الصف {row_idx}: الاسم ورقم الجوال مطلوبان.")
                        continue

                    if phone_s in seen_phone_rows:
                        # نعتبره تكرار داخل الملف ونتجاهله بدون تحذير (سلوك متوقع حسب الطلب)
                        continue
                    seen_phone_rows.add(phone_s)

                    # Upsert: تحديد المعلم الموجود ثم تحديث بياناته عند اللزوم
                    teacher = Teacher.objects.filter(phone=phone_s).first()
                    if teacher is None and nat_s:
                        teacher = Teacher.objects.filter(national_id=nat_s).first()

                    if teacher is None:
                        try:
                            teacher = Teacher.objects.create(
                                name=name_s,
                                phone=phone_s,
                                national_id=nat_s,
                                password=make_password(phone_s),  # كلمة المرور الافتراضية هي رقم الجوال
                            )
                            # ضبط الدور الافتراضي للتوافق
                            try:
                                teacher.role = Role.objects.filter(slug="teacher").first()
                                teacher.save(update_fields=["role"])
                            except Exception:
                                pass
                            created_count += 1
                        except (IntegrityError, ValidationError):
                            errors.append(f"الصف {row_idx}: تعذّر إنشاء المستخدم بسبب تعارض في رقم الجوال/الهوية.")
                            continue
                    else:
                        changed_fields: list[str] = []

                        # ✅ تحديث الاسم إذا اختلف
                        try:
                            if name_s and (getattr(teacher, "name", "") or "").strip() != name_s:
                                teacher.name = name_s
                                changed_fields.append("name")
                        except Exception:
                            pass

                        # ✅ تحديث رقم الهوية (إن وُجد)
                        if nat_s:
                            try:
                                current_nat = (getattr(teacher, "national_id", None) or "").strip() or None
                                if current_nat != nat_s:
                                    # تأكد أن الهوية ليست مرتبطة بمستخدم آخر
                                    nat_owner = Teacher.objects.filter(national_id=nat_s).exclude(pk=teacher.pk).first()
                                    if nat_owner is None:
                                        teacher.national_id = nat_s
                                        changed_fields.append("national_id")
                                    else:
                                        errors.append(
                                            f"الصف {row_idx}: رقم الهوية مرتبط بمستخدم آخر، لا يمكن تحديثه تلقائياً."
                                        )
                                        continue
                            except Exception:
                                pass

                        # ✅ تحديث رقم الجوال إذا تم العثور على المعلم عبر الهوية (أو اختلاف الجوال)
                        try:
                            current_phone = (getattr(teacher, "phone", "") or "").strip()
                            if phone_s and current_phone != phone_s:
                                phone_owner = Teacher.objects.filter(phone=phone_s).exclude(pk=teacher.pk).first()
                                if phone_owner is None:
                                    teacher.phone = phone_s
                                    changed_fields.append("phone")
                                    # لو تغير الجوال نحدّث كلمة المرور الافتراضية لتبقى متوافقة (اختياري)
                                    try:
                                        teacher.password = make_password(phone_s)
                                        changed_fields.append("password")
                                    except Exception:
                                        pass
                                else:
                                    errors.append(
                                        f"الصف {row_idx}: رقم الجوال مرتبط بمستخدم آخر، لا يمكن تحديثه تلقائياً."
                                    )
                                    continue
                        except Exception:
                            pass

                        if changed_fields:
                            try:
                                # حفظ الحقول التي تغيرت فقط
                                teacher.save(update_fields=list(dict.fromkeys(changed_fields)))
                                updated_count += 1
                            except Exception:
                                # إن فشل التحديث لسبب غير متوقع، نعتبره خطأ صف ونكمل
                                errors.append(f"الصف {row_idx}: تعذّر تحديث بيانات المستخدم.")
                                continue

                    # ربط المعلم بالمدرسة
                    try:
                        membership, created = SchoolMembership.objects.get_or_create(
                            school=active_school,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.TEACHER,
                            defaults={
                                "is_active": True,
                            },
                        )
                    except ValidationError as ve:
                        msg = " ".join(getattr(ve, "messages", []) or []) or str(ve)
                        errors.append(f"الصف {row_idx}: {msg}")
                        continue

                    if not created:
                        # إن كانت العضوية موجودة لكنها غير نشطة، فعّلها
                        try:
                            if hasattr(membership, "is_active") and not bool(getattr(membership, "is_active", True)):
                                membership.is_active = True
                                membership.save(update_fields=["is_active"])
                                reactivated_count += 1
                        except Exception:
                            pass

            if created_count > 0:
                messages.success(request, f"✅ تم إنشاء {created_count} معلّم جديد.")
            if updated_count > 0:
                messages.info(request, f"تم تحديث بيانات {updated_count} معلّم موجود.")
            if reactivated_count > 0:
                messages.info(request, f"تم تفعيل {reactivated_count} عضوية موجودة سابقاً.")
            if errors:
                for err in errors[:10]:
                    messages.warning(request, err)
                if len(errors) > 10:
                    messages.warning(request, f"... وهناك {len(errors)-10} أخطاء أخرى.")

            return redirect("reports:manage_teachers")

        except Exception:
            logger.exception("Bulk import failed")
            messages.error(request, "تعذّر معالجة الملف. تأكد أنه ملف .xlsx صحيح ومطابق للتعليمات.")

    return render(request, "reports/bulk_import_teachers.html")

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def add_teacher(request: HttpRequest) -> HttpResponse:
    # كل معلم جديد يُربط تلقائياً بالمدرسة النشطة لهذا المدير
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    if request.method == "POST":
        # إنشاء معلّم فقط: بدون قسم/بدون دور داخل قسم. التكاليف تتم من صفحة أعضاء القسم.
        form = TeacherCreateForm(request.POST)
        job_title = None
        try:
            # يحدد المسمى الوظيفي داخل المدرسة (بنفس الصلاحيات)
            job_title = (request.POST.get("job_title") or "").strip() or None
        except Exception:
            job_title = None

        # ✅ إذا كان رقم الجوال موجودًا مسبقًا: لا ننشئ مستخدمًا جديدًا، بل نربطه بهذه المدرسة
        try:
            phone_raw = (request.POST.get("phone") or "").strip()
            existing_teacher = None
            if phone_raw:
                existing_teacher = Teacher.objects.filter(phone=phone_raw).first()
            if existing_teacher is not None and active_school is not None:
                # تأكيد: صفحة "إضافة معلم" يجب أن تجعل الدور Teacher
                # (لتوافق عرض "القسم/الدور" في شاشة إدارة المعلمين)
                try:
                    if getattr(existing_teacher, "role_id", None) is None:
                        role_obj, _ = Role.objects.get_or_create(
                            slug="teacher",
                            defaults={
                                "name": "المعلم",
                                "is_staff_by_default": False,
                                "can_view_all_reports": False,
                                "is_active": True,
                            },
                        )
                        existing_teacher.role = role_obj
                        existing_teacher.save(update_fields=["role"])
                except Exception:
                    pass

                # هل هو مرتبط فعلاً بهذه المدرسة كـ TEACHER؟
                already = SchoolMembership.objects.filter(
                    school=active_school,
                    teacher=existing_teacher,
                    role_type=SchoolMembership.RoleType.TEACHER,
                    is_active=True,
                ).exists()
                if already:
                    messages.info(request, "المستخدم مرتبط بالفعل بهذه المدرسة.")
                    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                    return redirect(next_url or "reports:manage_teachers")

                # نفس منطق حد الباقة الحالي (مع ترك الضمان النهائي للموديل)
                try:
                    sub = getattr(active_school, "subscription", None)
                    if sub is None or bool(getattr(sub, "is_expired", True)):
                        messages.error(request, "لا يوجد اشتراك فعّال لهذه المدرسة.")
                        return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})

                    max_teachers = int(getattr(getattr(sub, "plan", None), "max_teachers", 0) or 0)
                    if max_teachers > 0:
                        current_count = SchoolMembership.objects.filter(
                            school=active_school,
                            role_type=SchoolMembership.RoleType.TEACHER,
                        ).count()
                        if current_count >= max_teachers:
                            messages.error(request, f"لا يمكن إضافة أكثر من {max_teachers} معلّم لهذه المدرسة حسب الباقة.")
                            return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})
                except Exception:
                    pass

                try:
                    with transaction.atomic():
                        SchoolMembership.objects.update_or_create(
                            school=active_school,
                            teacher=existing_teacher,
                            role_type=SchoolMembership.RoleType.TEACHER,
                            defaults={
                                "is_active": True,
                                **({"job_title": job_title} if job_title else {}),
                            },
                        )
                    messages.success(request, "✅ تم ربط المستخدم الموجود بهذه المدرسة بنجاح (بدون إنشاء حساب جديد).")
                    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                    return redirect(next_url or "reports:manage_teachers")
                except ValidationError as e:
                    messages.error(request, " ".join(getattr(e, "messages", []) or [str(e)]))
                except Exception:
                    logger.exception("add_teacher link existing failed")
                    messages.error(request, "حدث خطأ غير متوقع أثناء الربط. جرّب لاحقًا.")
        except Exception:
            # لو فشل هذا المسار لأي سبب نكمل التدفق الطبيعي (وقد يظهر خطأ unique من الفورم)
            pass

        # ✅ منع إضافة معلّم إذا تجاوزت المدرسة حد الباقة (يشمل غير النشط)
        try:
            if active_school is not None:
                sub = getattr(active_school, "subscription", None)
                if sub is None or bool(getattr(sub, "is_expired", True)):
                    messages.error(request, "لا يوجد اشتراك فعّال لهذه المدرسة.")
                    return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})

                max_teachers = int(getattr(getattr(sub, "plan", None), "max_teachers", 0) or 0)
                if max_teachers > 0:
                    current_count = SchoolMembership.objects.filter(
                        school=active_school,
                        role_type=SchoolMembership.RoleType.TEACHER,
                    ).count()
                    if current_count >= max_teachers:
                        messages.error(request, f"لا يمكن إضافة أكثر من {max_teachers} معلّم لهذه المدرسة حسب الباقة.")
                        return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})
        except Exception:
            # في حال خطأ غير متوقع، نكمل المسار الطبيعي (وسيمنعنا model validation عند الحفظ)
            pass

        if form.is_valid():
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)
                    # ربط المعلّم بالمدرسة الحالية كـ TEACHER
                    if active_school is not None:
                        SchoolMembership.objects.update_or_create(
                            school=active_school,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.TEACHER,
                            defaults={
                                "is_active": True,
                                **({"job_title": job_title} if job_title else {}),
                            },
                        )
                messages.success(request, "✅ تم إضافة المستخدم بنجاح.")
                next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                return redirect(next_url or "reports:manage_teachers")
            except IntegrityError:
                messages.error(request, "تعذّر الحفظ: قد يكون رقم الجوال أو الهوية مستخدمًا مسبقًا.")
            except ValidationError as e:
                # مثال: تجاوز حد المعلمين حسب الباقة أو عدم وجود اشتراك فعّال
                messages.error(request, " ".join(getattr(e, "messages", []) or [str(e)]))
            except Exception:
                logger.exception("add_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء الحفظ. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherCreateForm()
    return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def edit_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    teacher = get_object_or_404(Teacher, pk=pk)

    # لا يُسمح للمدير بتعديل معلّم غير مرتبط بمدرسته
    if not getattr(request.user, "is_superuser", False) and active_school is not None:
        has_membership = SchoolMembership.objects.filter(
            school=active_school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
            is_active=True,
        ).exists()
        if not has_membership:
            messages.error(request, "لا يمكنك تعديل هذا المعلّم لأنه غير مرتبط بمدرستك.")
            return redirect("reports:manage_teachers")
    if request.method == "POST":
        # تعديل بيانات المعلّم فقط — التكاليف تتم من صفحة أعضاء القسم
        form = TeacherEditForm(request.POST, instance=teacher, active_school=active_school)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(commit=True)
                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                return redirect(next_url or "reports:manage_teachers")
            except Exception:
                logger.exception("edit_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherEditForm(instance=teacher, active_school=active_school)

    return render(request, "reports/edit_teacher.html", {"form": form, "teacher": teacher, "title": "تعديل مستخدم"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def delete_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    teacher = get_object_or_404(Teacher, pk=pk)

    # لا يُسمح للمدير بحذف معلّم غير مرتبط بمدرسته
    if not getattr(request.user, "is_superuser", False) and active_school is not None:
        has_membership = SchoolMembership.objects.filter(
            school=active_school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
            is_active=True,
        ).exists()
        if not has_membership:
            messages.error(request, "لا يمكنك حذف هذا المعلّم لأنه غير مرتبط بمدرستك.")
            return redirect("reports:manage_teachers")
    try:
        with transaction.atomic():
            if active_school is not None and not getattr(request.user, "is_superuser", False):
                # ✅ في وضع تعدد المدارس: لا نحذف الحساب عالميًا، بل نفصل عضويته عن هذه المدرسة فقط
                SchoolMembership.objects.filter(
                    school=active_school,
                    teacher=teacher,
                    role_type__in=[
                        SchoolMembership.RoleType.TEACHER,
                        SchoolMembership.RoleType.REPORT_VIEWER,
                    ],
                ).delete()
                messages.success(request, "🗑️ تم إزالة المستخدم من المدرسة الحالية.")
            else:
                teacher.delete()
                messages.success(request, "🗑️ تم حذف المستخدم.")
    except Exception:
        logger.exception("delete_teacher failed")
        messages.error(request, "تعذّر حذف المستخدم. حاول لاحقًا.")
    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:manage_teachers")
