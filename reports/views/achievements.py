# reports/views/achievements.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _is_manager_in_school, _private_comment_role_label,
    _model_has_field, _get_active_school, _school_teachers_obj_label,
    _is_report_viewer, _user_manager_schools,
)


def _ensure_achievement_sections(ach_file: TeacherAchievementFile) -> None:
    """يضمن وجود 11 محورًا ثابتًا داخل الملف."""
    existing = set(
        AchievementSection.objects.filter(file=ach_file).values_list("code", flat=True)
    )
    to_create = []
    for code, title in AchievementSection.Code.choices:
        if int(code) in existing:
            continue
        to_create.append(
            AchievementSection(file=ach_file, code=int(code), title=str(title))
        )
    if to_create:
        AchievementSection.objects.bulk_create(to_create)


def _can_manage_achievement(user, active_school: Optional[School]) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if active_school is None:
        return False
    try:
        return SchoolMembership.objects.filter(
            teacher=user,
            school=active_school,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).exists()
    except Exception:
        return False


def _can_view_achievement(user, active_school: Optional[School]) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if is_platform_admin(user) and platform_can_access_school(user, active_school):
        return True
    if _can_manage_achievement(user, active_school):
        return True
    return False


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def achievement_my_files(request: HttpRequest) -> HttpResponse:
    """قائمة ملفات الإنجاز الخاصة بالمعلّم + إنشاء سنة جديدة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر/حدّد مدرسة أولاً.")
        return redirect("reports:home")

    existing_years = list(
        TeacherAchievementFile.objects.filter(school=active_school)
        .values_list("academic_year", flat=True)
        .distinct()
    )
    
    # استخراج السنوات المسموحة من إعدادات المدرسة
    allowed = active_school.allowed_academic_years if active_school else []
    
    create_form = AchievementCreateYearForm(
        request.POST or None, 
        year_choices=existing_years,
        allowed_years=allowed
    )
    if request.method == "POST" and (request.POST.get("action") == "create"):
        if create_form.is_valid():
            year = create_form.cleaned_data["academic_year"]
            ach_file, created = TeacherAchievementFile.objects.get_or_create(
                teacher=request.user,
                school=active_school,
                academic_year=year,
                defaults={},
            )
            _ensure_achievement_sections(ach_file)
            if created:
                messages.success(request, "تم إنشاء ملف الإنجاز للسنة بنجاح ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)
        messages.error(request, "تحقق من السنة الدراسية وأعد المحاولة.")

    files = (
        TeacherAchievementFile.objects.filter(teacher=request.user, school=active_school)
        .order_by("-academic_year", "-id")
    )
    return render(
        request,
        "reports/achievement_my_files.html",
        {"files": files, "create_form": create_form, "current_school": active_school},
    )


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def achievement_file_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """حذف ملف إنجاز (للمالك فقط)."""
    file = get_object_or_404(TeacherAchievementFile, pk=pk, teacher=request.user)
    
    # يمكن إضافة شرط الحالة لو أردنا منع حذف المعتمد، لكن السؤال يوحي بالحرية للتصحيح
    file.delete()
    messages.success(request, "تم حذف ملف الإنجاز بنجاح ✅")
    return redirect("reports:achievement_my_files")


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def achievement_file_update_year(request: HttpRequest, pk: int) -> HttpResponse:
    """تصحيح السنة الدراسية لملف الإنجاز (للمالك فقط)."""
    file = get_object_or_404(TeacherAchievementFile, pk=pk, teacher=request.user)
    active_school = _get_active_school(request)

    # نموذج بسيط للتحقق من السنة (نستخدم نفس فورم الإنشاء للتحقق مع تمرير القيمة المرسلة كخيار مقبول)
    # هذا يسمح بقبول أي سنة صحيحة (هيئة + تتابع) حتى لو لم تكن في القائمة الافتراضية
    submitted_year = request.POST.get("academic_year", "")
    form = AchievementCreateYearForm(request.POST, year_choices=[submitted_year]) 
    
    if form.is_valid():
        new_year = form.cleaned_data["academic_year"]
        
        # 1. التحقق من عدم التكرار
        duplicate = TeacherAchievementFile.objects.filter(
            teacher=request.user, 
            school=file.school, 
            academic_year=new_year
        ).exclude(pk=file.pk).exists()

        if duplicate:
            messages.error(request, f" لا يمكن التعديل: لديك ملف آخر بالفعل للسنة {new_year}")
        else:
            file.academic_year = new_year
            file.save(update_fields=["academic_year", "updated_at"])
            messages.success(request, f"تم تعديل السنة الدراسية إلى {new_year} ✅")

    else:
        # استخراج أول خطأ
        err = next(iter(form.errors.values()))[0] if form.errors else "بيانات غير صالحة"
        messages.error(request, f"تعذر تحديث السنة: {err}")

    return redirect("reports:achievement_my_files")


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def achievement_school_files(request: HttpRequest) -> HttpResponse:
    """قائمة ملفات الإنجاز للمدرسة (مدير/مشرف عرض فقط).

    - تعرض جميع المعلمين في المدرسة النشطة.
    - بجانب كل معلم: فتح الملف + طباعة/حفظ PDF.
    - الاعتماد/الرفض يكون داخل صفحة الملف نفسها.
    """
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر/حدّد مدرسة أولاً.")
        return redirect("reports:home")

    if not _can_view_achievement(request.user, active_school):
        messages.error(request, "لا تملك صلاحية الاطلاع على ملفات الإنجاز.")
        return redirect("reports:home")

    # اختيار سنة (اختياري): إن لم تُحدد، نأخذ آخر سنة موجودة في المدرسة
    year = (request.GET.get("year") or request.POST.get("year") or "").strip()
    try:
        year = year.replace("–", "-").replace("—", "-")
    except Exception:
        pass

    existing_years = list(
        TeacherAchievementFile.objects.filter(school=active_school)
        .values_list("academic_year", flat=True)
        .distinct()
    )
    # نفس منطق الاختيارات في نموذج الإنشاء (بدون إدخال يدوي)
    tmp_form = AchievementCreateYearForm(year_choices=existing_years)
    year_choices = [c[0] for c in tmp_form.fields["academic_year"].choices]

    if not year and year_choices:
        year = year_choices[0]
    if year and year_choices and year not in year_choices:
        year = year_choices[0]

    base_url = reverse("reports:achievement_school_files")

    def _redirect_with_year(year_value: str) -> HttpResponse:
        year_value = (year_value or "").strip()
        if not year_value:
            return redirect(base_url)
        return redirect(f"{base_url}?{urlencode({'year': year_value})}")

    # إنشاء ملف إنجاز من صفحة المدرسة غير مسموح: المعلّم هو من ينشئ ملفه من (ملف الإنجاز)
    if request.method == "POST" and (request.POST.get("action") == "create"):
        messages.error(request, "إنشاء ملف الإنجاز متاح للمعلّم فقط.")
        return _redirect_with_year(year)

    # Search Logic
    q = request.GET.get("q", "").strip()

    teachers = (
        Teacher.objects.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
        )
        .distinct()
        .only("id", "name", "phone", "national_id")
        .order_by("name")
    )
    
    if q:
        from django.db.models import Q
        from django.db.models import Prefetch
        teachers = teachers.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(national_id__icontains=q)
        )

    files_by_teacher_id = {}
    if year:
        files = (
            TeacherAchievementFile.objects.filter(school=active_school, academic_year=year)
            .select_related("teacher")
            .only("id", "teacher_id", "status", "academic_year")
        )
        if q:
            # تصفية الملفات أيضاً لتحسين الأداء
            files = files.filter(teacher__in=teachers)

        files_by_teacher_id = {f.teacher_id: f for f in files}

    rows = [{"teacher": t, "file": files_by_teacher_id.get(t.id)} for t in teachers]

    return render(
        request,
        "reports/achievement_school_files.html",
        {
            "rows": rows,
            "year": year,
            "year_choices": year_choices,
            "current_school": active_school,
            "is_manager": _can_manage_achievement(request.user, active_school),
            "q": q,
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def achievement_school_teachers(request: HttpRequest) -> HttpResponse:
    """Alias قديم: توجيه إلى صفحة المدرسة الموحدة."""
    params = {}
    year = (request.GET.get("year") or request.POST.get("year") or "").strip()
    if year:
        params["year"] = year
    url = reverse("reports:achievement_school_files")
    if params:
        return redirect(f"{url}?{urlencode(params)}")
    return redirect("reports:achievement_school_files")


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def achievement_file_detail(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    ach_file = get_object_or_404(TeacherAchievementFile, pk=pk)
    user = request.user

    if not getattr(user, "is_superuser", False):
        if active_school is None or ach_file.school_id != getattr(active_school, "id", None):
            raise Http404

    is_manager = _can_manage_achievement(user, active_school)
    is_viewer = _is_report_viewer(user, active_school)
    is_owner = (ach_file.teacher_id == getattr(user, "id", None))
    is_platform = bool(is_platform_admin(user) and platform_can_access_school(user, active_school))

    if not (getattr(user, "is_superuser", False) or is_manager or is_viewer or is_owner or is_platform):
        return HttpResponse(status=403)

    _ensure_achievement_sections(ach_file)
    try:
        from django.db.models import Prefetch

        ev_reports_qs = AchievementEvidenceReport.objects.select_related(
            "report",
            "report__category",
        ).order_by("id")
        sections = (
            AchievementSection.objects.filter(file=ach_file)
            .prefetch_related("evidence_images", Prefetch("evidence_reports", queryset=ev_reports_qs))
            .order_by("code", "id")
        )
    except Exception:
        sections = (
            AchievementSection.objects.filter(file=ach_file)
            .prefetch_related("evidence_images", "evidence_reports")
            .order_by("code", "id")
        )

    can_edit_teacher = bool(is_owner and ach_file.status in {TeacherAchievementFile.Status.DRAFT, TeacherAchievementFile.Status.RETURNED})
    can_post = bool((can_edit_teacher or is_manager) and not is_viewer)

    general_form = TeacherAchievementFileForm(request.POST or None, instance=ach_file)
    manager_notes_form = AchievementManagerNotesForm(request.POST or None, instance=ach_file)
    year_form = AchievementCreateYearForm()
    upload_form = AchievementEvidenceUploadForm()

    # تعليقات خاصة (يراها المعلم + أصحاب الصلاحية داخل المدرسة/المنصة)
    is_staff_user = _is_staff(user)
    can_add_private_comment = bool(is_platform or is_manager or is_staff_user or getattr(user, "is_superuser", False))
    show_private_comments = bool(is_owner or can_add_private_comment)
    private_comments = (
        TeacherPrivateComment.objects.select_related("created_by")
        .filter(achievement_file=ach_file, teacher=ach_file.teacher)
        .order_by("-created_at", "-id")
        if show_private_comments
        else TeacherPrivateComment.objects.none()
    )
    private_comment_form = PrivateCommentForm(request.POST or None) if can_add_private_comment else None

    # الرجوع ثابت حسب الدور: المعلّم -> ملفاتي، غير ذلك -> ملفات المدرسة
    if is_owner:
        back_url = reverse("reports:achievement_my_files")
    else:
        url = reverse("reports:achievement_school_files")
        back_url = f"{url}?{urlencode({'year': ach_file.academic_year})}"

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        section_id = request.POST.get("section_id")

        # ===== تعليقات خاصة (لا تظهر في الطباعة أو المشاركة) =====
        # توافق خلفي: platform_comment
        if action in {"platform_comment", "private_comment_create", "private_comment_update", "private_comment_delete"}:
            if not can_add_private_comment:
                return HttpResponse(status=403)

            # create
            if action in {"platform_comment", "private_comment_create"}:
                if private_comment_form is not None and private_comment_form.is_valid():
                    body = private_comment_form.cleaned_data["body"]
                    try:
                        with transaction.atomic():
                            TeacherPrivateComment.objects.create(
                                teacher=ach_file.teacher,
                                created_by=user,
                                school=active_school,
                                achievement_file=ach_file,
                                body=body,
                            )
                            n = Notification.objects.create(
                                title="تعليق خاص على ملف الإنجاز",
                                message=body,
                                is_important=True,
                                school=active_school,
                                created_by=user,
                            )
                            NotificationRecipient.objects.create(notification=n, teacher=ach_file.teacher)
                        messages.success(request, "تم إرسال التعليق الخاص للمعلّم ✅")
                        return redirect("reports:achievement_file_detail", pk=ach_file.pk)
                    except Exception:
                        logger.exception("Failed to create private achievement comment")
                        messages.error(request, "تعذر حفظ التعليق. حاول مرة أخرى.")
                else:
                    messages.error(request, "تحقق من نص التعليق وأعد المحاولة.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            # update/delete (only comment owner, or superuser)
            comment_id = request.POST.get("comment_id")
            try:
                comment_id_int = int(comment_id) if comment_id else None
            except (TypeError, ValueError):
                comment_id_int = None

            if not comment_id_int:
                messages.error(request, "تعذر تحديد التعليق.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            comment = TeacherPrivateComment.objects.filter(
                pk=comment_id_int,
                achievement_file=ach_file,
                teacher=ach_file.teacher,
            ).first()
            if comment is None:
                messages.error(request, "التعليق غير موجود.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            is_owner_of_comment = getattr(comment, "created_by_id", None) == getattr(user, "id", None)

            if action == "private_comment_update":
                # تعديل: لصاحب التعليق فقط
                if not is_owner_of_comment:
                    return HttpResponse(status=403)
                body = (request.POST.get("body") or "").strip()
                if not body:
                    messages.error(request, "نص التعليق مطلوب.")
                    return redirect("reports:achievement_file_detail", pk=ach_file.pk)
                try:
                    TeacherPrivateComment.objects.filter(pk=comment.pk).update(body=body)
                    messages.success(request, "تم تعديل التعليق ✅")
                except Exception:
                    messages.error(request, "تعذر تعديل التعليق.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            if action == "private_comment_delete":
                # حذف: لصاحب التعليق فقط، والسوبر يمكنه حذف أي تعليق
                if not (is_owner_of_comment or getattr(user, "is_superuser", False)):
                    return HttpResponse(status=403)
                try:
                    comment.delete()
                    messages.success(request, "تم حذف التعليق ✅")
                except Exception:
                    messages.error(request, "تعذر حذف التعليق.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        if not can_post:
            return HttpResponse(status=403)

        if action == "save_general" and can_edit_teacher:
            if general_form.is_valid():
                general_form.save()
                messages.success(request, "تم حفظ البيانات العامة ✅")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)
            messages.error(request, "تحقق من الحقول وأعد المحاولة.")

        elif action == "save_section" and can_edit_teacher and section_id:
            sec = get_object_or_404(AchievementSection, pk=int(section_id), file=ach_file)
            sec_form = AchievementSectionNotesForm(request.POST, instance=sec)
            if sec_form.is_valid():
                sec_form.save()
                messages.success(request, "تم حفظ ملاحظات المحور ✅")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)
            messages.error(request, "تحقق من ملاحظات المحور وأعد المحاولة.")

        elif action == "upload_evidence" and can_edit_teacher and section_id:
            sec = get_object_or_404(AchievementSection, pk=int(section_id), file=ach_file)
            imgs = request.FILES.getlist("images")
            if not imgs:
                messages.error(request, "اختر صورًا للرفع.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)
            existing_count = AchievementEvidenceImage.objects.filter(section=sec).count()
            remaining = max(0, 8 - existing_count)
            if remaining <= 0:
                messages.error(request, "لا يمكن إضافة أكثر من 8 صور لهذا المحور.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)
            imgs = imgs[:remaining]
            for f in imgs:
                AchievementEvidenceImage.objects.create(section=sec, image=f)
            messages.success(request, "تم رفع الشواهد ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "delete_evidence" and can_edit_teacher:
            img_id = request.POST.get("image_id")
            if img_id:
                img = get_object_or_404(AchievementEvidenceImage, pk=int(img_id), section__file=ach_file)
                try:
                    img.delete()
                except Exception:
                    pass
                messages.success(request, "تم حذف الصورة ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "add_report_evidence" and can_edit_teacher and section_id:
            sec = get_object_or_404(AchievementSection, pk=int(section_id), file=ach_file)
            report_id = request.POST.get("report_id")
            try:
                report_id_int = int(report_id) if report_id else None
            except (TypeError, ValueError):
                report_id_int = None
            if not report_id_int:
                messages.error(request, "تعذر تحديد التقرير.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            rep_qs = Report.objects.select_related("category").filter(teacher=request.user)
            try:
                if active_school is not None and _model_has_field(Report, "school"):
                    rep_qs = rep_qs.filter(school=active_school)
            except Exception:
                pass
            r = get_object_or_404(rep_qs, pk=report_id_int)

            try:
                add_report_evidence(section=sec, report=r)
                messages.success(request, "تم إضافة التقرير كشاهِد ✅")
            except Exception:
                messages.error(request, "تعذر إضافة التقرير. ربما تمت إضافته مسبقاً.")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "delete_report_evidence" and can_edit_teacher and section_id:
            sec = get_object_or_404(AchievementSection, pk=int(section_id), file=ach_file)
            evidence_id = request.POST.get("evidence_id")
            try:
                evidence_id_int = int(evidence_id) if evidence_id else None
            except (TypeError, ValueError):
                evidence_id_int = None
            if not evidence_id_int:
                messages.error(request, "تعذر تحديد الشاهد.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            try:
                ok = remove_report_evidence(section=sec, evidence_id=evidence_id_int)
                if ok:
                    messages.success(request, "تم إزالة التقرير من الشواهد ✅")
                else:
                    messages.error(request, "الشاهد غير موجود.")
            except Exception:
                messages.error(request, "تعذر إزالة الشاهد.")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "import_prev" and can_edit_teacher:
            prev_year = (request.POST.get("prev_year") or "").strip()
            if prev_year:
                prev = TeacherAchievementFile.objects.filter(
                    teacher=ach_file.teacher,
                    school=ach_file.school,
                    academic_year=prev_year,
                ).first()
            else:
                prev = (
                    TeacherAchievementFile.objects.filter(
                        teacher=ach_file.teacher, school=ach_file.school
                    )
                    .exclude(pk=ach_file.pk)
                    .order_by("-academic_year", "-id")
                    .first()
                )
            if not prev:
                messages.error(request, "لا يوجد ملف سابق للاستيراد.")
                return redirect("reports:achievement_file_detail", pk=ach_file.pk)

            # استيراد الحقول الثابتة فقط
            ach_file.qualifications = prev.qualifications
            ach_file.professional_experience = prev.professional_experience
            ach_file.specialization = prev.specialization
            ach_file.teaching_load = prev.teaching_load
            ach_file.subjects_taught = prev.subjects_taught
            ach_file.contact_info = prev.contact_info
            ach_file.save(update_fields=[
                "qualifications",
                "professional_experience",
                "specialization",
                "teaching_load",
                "subjects_taught",
                "contact_info",
                "updated_at",
            ])
            messages.success(request, "تم استيراد البيانات الثابتة من ملف سابق ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "submit" and can_edit_teacher:
            now = timezone.now()
            try:
                with transaction.atomic():
                    ach_file.status = TeacherAchievementFile.Status.SUBMITTED
                    ach_file.submitted_at = now
                    ach_file.save(update_fields=["status", "submitted_at", "updated_at"])

                    frozen = freeze_achievement_report_evidences(ach_file=ach_file)
                if frozen:
                    messages.success(request, f"تم إرسال الملف للاعتماد ✅ (تم تجميد {frozen} تقرير/تقارير كشواهد)")
                else:
                    messages.success(request, "تم إرسال الملف للاعتماد ✅")
            except Exception:
                # حتى لو فشل التجميد لأي سبب، لا نكسر تجربة المستخدم
                messages.success(request, "تم إرسال الملف للاعتماد ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "approve" and is_manager:
            ach_file.status = TeacherAchievementFile.Status.APPROVED
            ach_file.decided_at = timezone.now()
            ach_file.decided_by = request.user
            ach_file.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
            messages.success(request, "تم اعتماد ملف الإنجاز ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        elif action == "return" and is_manager:
            if manager_notes_form.is_valid():
                manager_notes_form.save()
            ach_file.status = TeacherAchievementFile.Status.RETURNED
            ach_file.decided_at = timezone.now()
            ach_file.decided_by = request.user
            ach_file.save(update_fields=["status", "decided_at", "decided_by", "updated_at", "manager_notes"])
            messages.success(request, "تم إرجاع الملف للمعلّم مع الملاحظات ✅")
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        messages.error(request, "تعذر تنفيذ العملية.")

    try:
        if show_private_comments and private_comments is not None:
            for c in private_comments:
                try:
                    c.created_by_role_label = _private_comment_role_label(getattr(c, "created_by", None), active_school)
                except Exception:
                    c.created_by_role_label = ""
    except Exception:
        pass

    return render(
        request,
        "reports/achievement_file.html",
        {
            "file": ach_file,
            "sections": sections,
            "general_form": general_form,
            "upload_form": upload_form,
            "manager_notes_form": manager_notes_form,
            "can_edit_teacher": can_edit_teacher,
            "is_manager": is_manager,
            "is_viewer": is_viewer,
            "is_owner": is_owner,
            "show_private_comments": show_private_comments,
            "private_comments": private_comments,
            "private_comment_form": private_comment_form,
            "can_add_private_comment": can_add_private_comment,
            "current_user_id": getattr(user, "id", None),
            "is_superuser": bool(getattr(user, "is_superuser", False)),
            "year_form": year_form,
            "current_school": active_school,
            "back_url": back_url,
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def achievement_report_picker(request: HttpRequest, pk: int) -> HttpResponse:
    """Return a partial HTML list to pick teacher reports as evidence for a section."""

    active_school = _get_active_school(request)
    ach_file = get_object_or_404(TeacherAchievementFile, pk=pk)
    user = request.user

    is_owner = (ach_file.teacher_id == getattr(user, "id", None))
    if not is_owner:
        return HttpResponse(status=403)
    if not getattr(user, "is_superuser", False):
        if active_school is None or ach_file.school_id != getattr(active_school, "id", None):
            raise Http404

    if ach_file.status not in {
        TeacherAchievementFile.Status.DRAFT,
        TeacherAchievementFile.Status.RETURNED,
    }:
        return HttpResponse(status=403)

    section_id = request.GET.get("section_id")
    try:
        section_id_int = int(section_id) if section_id else None
    except (TypeError, ValueError):
        section_id_int = None
    if not section_id_int:
        return HttpResponse(status=400)

    section = get_object_or_404(AchievementSection, pk=section_id_int, file=ach_file)
    q = (request.GET.get("q") or "").strip()

    qs = achievement_picker_reports_qs(teacher=user, active_school=active_school, q=q).select_related("category")
    reports = list(qs[:50])
    already_ids = set(
        AchievementEvidenceReport.objects.filter(section=section, report__isnull=False).values_list(
            "report_id", flat=True
        )
    )

    return render(
        request,
        "reports/partials/achievement_report_picker_list.html",
        {
            "file": ach_file,
            "section": section,
            "reports": reports,
            "q": q,
            "already_ids": already_ids,
        },
    )


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="20/h", method="GET", block=True)
@require_http_methods(["GET"])
def achievement_file_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    ach_file = get_object_or_404(TeacherAchievementFile, pk=pk)
    if not getattr(request.user, "is_superuser", False):
        if active_school is None or ach_file.school_id != getattr(active_school, "id", None):
            raise Http404
    if not (_can_view_achievement(request.user, active_school) or ach_file.teacher_id == getattr(request.user, "id", None)):
        return HttpResponse(status=403)

    # توليد PDF عند الطلب
    try:
        from ..pdf_achievement import generate_achievement_pdf

        pdf_bytes, filename = generate_achievement_pdf(request=request, ach_file=ach_file)
    except OSError as ex:
        # WeasyPrint on Windows يحتاج مكتبات نظام (GTK/Pango/Cairo) مثل libgobject.
        msg = str(ex) or ""
        if "libgobject" in msg or "gobject-2.0" in msg:
            # أفضل UX: لا نعرض صفحة خطأ/نص؛ نرجع لنفس صفحة الملف برسالة واضحة.
            messages.error(
                request,
                "تعذر توليد PDF محليًا لأن مكتبات الطباعة غير مثبتة على هذا الجهاز. "
                "أفضل حل: شغّل المشروع على Render/Docker/WSL (Linux) أو ثبّت GTK runtime على Windows.",
            )
            logger.warning("WeasyPrint native deps missing: %s", msg)
            return redirect("reports:achievement_file_detail", pk=ach_file.pk)

        if settings.DEBUG:
            raise
        messages.error(request, "تعذر توليد ملف PDF حاليًا.")
        return redirect("reports:achievement_file_detail", pk=ach_file.pk)
    except Exception:
        if settings.DEBUG:
            raise
        messages.error(request, "تعذر توليد ملف PDF حاليًا.")
        return redirect("reports:achievement_file_detail", pk=ach_file.pk)

    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def achievement_file_print(request: HttpRequest, pk: int) -> HttpResponse:
    """صفحة طباعة ملف الإنجاز (مثل طباعة التقارير).

    تعتمد على الطباعة من المتصفح (Save as PDF) لتجنّب مشاكل WeasyPrint على Windows.
    """

    active_school = _get_active_school(request)
    ach_file = get_object_or_404(TeacherAchievementFile, pk=pk)

    if not getattr(request.user, "is_superuser", False):
        if active_school is None or ach_file.school_id != getattr(active_school, "id", None):
            raise Http404

    if not (_can_view_achievement(request.user, active_school) or ach_file.teacher_id == getattr(request.user, "id", None)):
        return HttpResponse(status=403)

    _ensure_achievement_sections(ach_file)
    try:
        from django.db.models import Prefetch

        ev_reports_qs = AchievementEvidenceReport.objects.select_related(
            "report",
            "report__category",
        ).order_by("id")
        sections = (
            AchievementSection.objects.filter(file=ach_file)
            .prefetch_related("evidence_images", Prefetch("evidence_reports", queryset=ev_reports_qs))
            .order_by("code", "id")
        )
        has_evidence_reports = AchievementEvidenceReport.objects.filter(section__file=ach_file).exists()
    except Exception:
        sections = (
            AchievementSection.objects.filter(file=ach_file)
            .prefetch_related("evidence_images", "evidence_reports")
            .order_by("code", "id")
        )
        has_evidence_reports = False

    school = ach_file.school
    primary = (getattr(school, "print_primary_color", None) or "").strip() or "#2563eb"

    # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
    school_logo_url = ""

    try:
        from ..pdf_achievement import _static_png_as_data_uri

        ministry_logo_src = _static_png_as_data_uri("img/UntiTtled-1.png")
    except Exception:
        ministry_logo_src = None

    # تحديد URL الرجوع الذكي حسب دور المستخدم
    back_url = "reports:achievement_my_files"  # الافتراضي للمعلم
    is_manager = _is_manager_in_school(request.user, active_school)
    is_staff_user = _is_staff(request.user)
    is_superuser_val = bool(getattr(request.user, "is_superuser", False))
    
    if is_superuser_val or is_manager or is_staff_user:
        back_url = "reports:achievement_school_files"
    
    return render(
        request,
        "reports/pdf/achievement_file.html",
        {
            "file": ach_file,
            "school": school,
            "sections": sections,
            "has_evidence_reports": has_evidence_reports,
            "theme": {"brand": primary},
            "now": timezone.localtime(timezone.now()),
            "school_logo_url": school_logo_url,
            "ministry_logo_src": ministry_logo_src,
            "back_url": back_url,
        },
    )


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def report_viewer_create(request: HttpRequest) -> HttpResponse:
    """مدير المدرسة ينشئ حساب مشرف تقارير (عرض فقط) داخل المدرسة النشطة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
        return redirect("reports:select_school")

    form = ManagerCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    # ✅ حد أقصى: 2 مشرفي تقارير نشطين لكل مدرسة
                    active_viewers = SchoolMembership.objects.filter(
                        school=active_school,
                        role_type=SchoolMembership.RoleType.REPORT_VIEWER,
                        is_active=True,
                    ).count()
                    if active_viewers >= 2:
                        messages.error(request, "لا يمكن إضافة أكثر من 2 مشرف تقارير (عرض فقط) لهذه المدرسة.")
                        raise ValidationError("viewer_limit")

                    viewer = form.save(commit=True)

                    # تأكيد: لا نعطي صلاحيات موظف لوحة ولا دور manager
                    try:
                        viewer_role = Role.objects.filter(slug="teacher").first()
                        viewer.role = viewer_role
                        viewer.is_staff = False
                        viewer.save(update_fields=["role", "is_staff"])
                    except Exception:
                        try:
                            viewer.is_staff = False
                            viewer.save(update_fields=["is_staff"])
                        except Exception:
                            viewer.save()

                    SchoolMembership.objects.update_or_create(
                        school=active_school,
                        teacher=viewer,
                        role_type=SchoolMembership.RoleType.REPORT_VIEWER,
                        defaults={"is_active": True},
                    )

                messages.success(request, "تم إنشاء حساب مشرف التقارير وربطه بالمدرسة بنجاح.")
                return redirect("reports:manage_teachers")
            except ValidationError as e:
                # رسائل الحد/التحقق
                if "viewer_limit" not in " ".join(getattr(e, "messages", []) or [str(e)]):
                    messages.error(request, " ".join(getattr(e, "messages", []) or [str(e)]))
            except Exception:
                logger.exception("report_viewer_create failed")
                messages.error(request, "تعذّر إنشاء مشرف التقارير. تحقّق من البيانات وحاول مرة أخرى.")
        else:
            messages.error(request, "فضلاً تحقق من الحقول وأعد المحاولة.")

    return render(
        request,
        "reports/add_teacher.html",
        {
            "form": form,
            "page_title": "إضافة مشرف  (عرض فقط)",
            "page_subtitle": "هذا الحساب يستطيع الاطلاع على تقارير المدرسة و ملفات الإنجاز فقط",
            "save_label": "حفظ المشرف",
            "back_url": "reports:manage_teachers",
            "back_label": f"رجوع لإدارة {_school_teachers_obj_label(active_school)}",
        },
    )


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def report_viewer_update(request: HttpRequest, pk: int) -> HttpResponse:
    """تعديل بيانات مشرف التقارير (عرض فقط) داخل المدرسة النشطة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
        return redirect("reports:select_school")

    viewer = get_object_or_404(Teacher, pk=pk)
    has_membership = SchoolMembership.objects.filter(
        school=active_school,
        teacher=viewer,
        role_type=SchoolMembership.RoleType.REPORT_VIEWER,
    ).exists()
    if not has_membership:
        messages.error(request, "هذا المستخدم ليس مشرف تقارير في المدرسة الحالية.")
        return redirect("reports:manage_teachers")

    form = ManagerCreateForm(request.POST or None, instance=viewer)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=True)
                    # ضمان عدم منحه صلاحيات موظف لوحة
                    try:
                        updated.is_staff = False
                        if getattr(getattr(updated, "role", None), "slug", None) == MANAGER_SLUG:
                            updated.role = Role.objects.filter(slug="teacher").first()
                        updated.save(update_fields=["is_staff", "role"])
                    except Exception:
                        pass
                messages.success(request, "✏️ تم تحديث بيانات مشرف التقارير.")
                return redirect("reports:manage_teachers")
            except Exception:
                logger.exception("report_viewer_update failed")
                messages.error(request, "تعذّر تحديث البيانات. حاول لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")

    return render(
        request,
        "reports/add_teacher.html",
        {
            "form": form,
            "page_title": "تعديل مشرف تقارير (عرض فقط)",
            "page_subtitle": "تعديل بيانات الحساب دون تغيير صلاحياته",
            "save_label": "حفظ التعديلات",
            "back_url": "reports:manage_teachers",
            "back_label": f"رجوع لإدارة {_school_teachers_obj_label(active_school)}",
        },
    )


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def report_viewer_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    """تفعيل/إيقاف مشرف التقارير داخل المدرسة النشطة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
        return redirect("reports:select_school")

    viewer = get_object_or_404(Teacher, pk=pk)
    membership = SchoolMembership.objects.filter(
        school=active_school,
        teacher=viewer,
        role_type=SchoolMembership.RoleType.REPORT_VIEWER,
    ).first()
    if membership is None:
        messages.error(request, "هذا المستخدم ليس مشرف تقارير في المدرسة الحالية.")
        return redirect("reports:manage_teachers")

    try:
        with transaction.atomic():
            target_active = not bool(membership.is_active)
            if target_active:
                # حد 2 مشرفين نشطين
                active_viewers = SchoolMembership.objects.filter(
                    school=active_school,
                    role_type=SchoolMembership.RoleType.REPORT_VIEWER,
                    is_active=True,
                ).exclude(pk=membership.pk).count()
                if active_viewers >= 2:
                    raise ValidationError("لا يمكن تفعيل أكثر من 2 مشرف تقارير (عرض فقط) لهذه المدرسة.")

            membership.is_active = target_active
            membership.save(update_fields=["is_active"])

            viewer.is_active = target_active
            viewer.save(update_fields=["is_active"])

        messages.success(request, "✅ تم تفعيل الحساب." if target_active else "⛔ تم إيقاف الحساب.")
    except ValidationError as e:
        messages.error(request, " ".join(getattr(e, "messages", []) or [str(e)]))
    except Exception:
        logger.exception("report_viewer_toggle failed")
        messages.error(request, "تعذّر تغيير حالة الحساب. حاول لاحقًا.")

    return redirect("reports:manage_teachers")


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def report_viewer_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """حذف (آمن) لمشرف التقارير من المدرسة: تعطيل الحساب وإزالة العضوية من المدرسة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
        return redirect("reports:select_school")

    viewer = get_object_or_404(Teacher, pk=pk)
    membership_qs = SchoolMembership.objects.filter(
        school=active_school,
        teacher=viewer,
        role_type=SchoolMembership.RoleType.REPORT_VIEWER,
    )
    if not membership_qs.exists():
        messages.error(request, "هذا المستخدم ليس مشرف تقارير في المدرسة الحالية.")
        return redirect("reports:manage_teachers")

    try:
        with transaction.atomic():
            viewer.is_active = False
            viewer.save(update_fields=["is_active"])
            # إزالة الربط حتى يختفي من القائمة
            membership_qs.delete()
        messages.success(request, "🗑️ تم حذف مشرف التقارير من المدرسة.")
    except Exception:
        logger.exception("report_viewer_delete failed")
        messages.error(request, "تعذّر حذف المستخدم. حاول لاحقًا.")
    return redirect("reports:manage_teachers")
