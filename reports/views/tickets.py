# reports/views/tickets.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _is_manager_in_school, _filter_by_school,
    _get_active_school, _user_manager_schools, _user_department_codes,
)


# =========================
def _can_act(user, ticket: Ticket) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    # 1. المشرف العام (تذاكر المنصة)
    if ticket.is_platform and getattr(user, "is_superuser", False):
        return True

    # 2. المستلم المباشر (Assignee)
    if ticket.assignee_id == user.id:
        return True

    # 2.1 المستلمون (Recipients)
    try:
        rel = getattr(ticket, "recipients", None)
        if rel is not None and rel.filter(id=user.id).exists():
            return True
    except Exception:
        pass

    # 3. مدير المدرسة (لتذاكر المدرسة)
    # يحق للمدير التحكم في أي تذكرة تابعة لمدرسته
    if not ticket.is_platform and ticket.school_id:
        if SchoolMembership.objects.filter(
            school_id=ticket.school_id,
            teacher=user,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True
        ).exists():
            return True

    # 3.1 المشرف العام (ضمن نطاقه) - لتذاكر المدرسة فقط
    if not ticket.is_platform and ticket.school_id:
        try:
            if is_platform_admin(user) and platform_allowed_schools_qs(user).filter(id=ticket.school_id).exists():
                return True
        except Exception:
            pass

    # 4. مسؤول القسم (Officer)
    # إذا كانت التذكرة تابعة لقسم، فمسؤول القسم يملك صلاحية عليها
    if ticket.department_id and DepartmentMembership is not None:
        if DepartmentMembership.objects.filter(
            department_id=ticket.department_id,
            teacher=user,
            role_type=DepartmentMembership.OFFICER
        ).exists():
            # عزل المدرسة: لمسؤول القسم، لا نسمح بالتعامل مع تذكرة مدرسة أخرى
            try:
                if (not ticket.is_platform) and ticket.school_id:
                    if not SchoolMembership.objects.filter(
                        teacher=user,
                        school_id=ticket.school_id,
                        is_active=True,
                    ).exists():
                        return False
            except Exception:
                pass
            return True

    return False

@login_required(login_url="reports:login")
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)

    # إذا كانت هناك مدارس مفعّلة، نلزم اختيار مدرسة لإنشاء تذكرة مدرسة
    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if request.method == "POST":
        form = TicketCreateForm(request.POST, request.FILES, user=request.user, active_school=active_school)
        if form.is_valid():
            ticket: Ticket = form.save(commit=True, user=request.user)  # يحفظ التذكرة والصور
            if hasattr(ticket, "school") and active_school is not None:
                ticket.school = active_school
                ticket.save(update_fields=["school"])
            messages.success(request, "✅ تم إرسال الطلب بنجاح.")
            return redirect("reports:my_requests")
        messages.error(request, "فضلاً تحقّق من الحقول.")
    else:
        form = TicketCreateForm(user=request.user, active_school=active_school)
    return render(request, "reports/request_create.html", {"form": form})

@login_required(login_url="reports:login")
@role_required({"manager"})
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def support_ticket_create(request: HttpRequest) -> HttpResponse:
    """إنشاء تذكرة دعم فني للمنصة (للمدراء فقط)"""
    from ..forms import SupportTicketForm
    
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    
    if request.method == "POST":
        form = SupportTicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False, user=request.user)
            if active_school:
                ticket.school = active_school
            ticket.save()
            messages.success(request, "✅ تم إرسال طلب الدعم الفني بنجاح.")
            return redirect("reports:my_support_tickets")
        messages.error(request, "فضلاً تحقّق من الحقول.")
    else:
        form = SupportTicketForm()
        
    return render(request, "reports/support_ticket_create.html", {"form": form})


@login_required(login_url="reports:login")
@role_required({"manager"})
def my_support_tickets(request: HttpRequest) -> HttpResponse:
    """عرض تذاكر الدعم الفني الخاصة بالمدير"""
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    tickets = Ticket.objects.filter(
        creator=request.user, 
        is_platform=True,
        school=active_school,
    ).order_by("-created_at")
    
    return render(request, "reports/my_support_tickets.html", {"tickets": tickets})


@login_required(login_url="reports:login")
def my_requests(request: HttpRequest) -> HttpResponse:
    user = request.user
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    notes_qs = (
        TicketNote.objects.filter(is_public=True)
        .select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    base_qs = _filter_by_school(
        Ticket.objects.select_related("assignee", "department")
        .prefetch_related("recipients")
        .prefetch_related(Prefetch("notes", queryset=notes_qs, to_attr="pub_notes"))
        .only("id", "title", "status", "department", "created_at", "assignee__name")
        .filter(creator=user, is_platform=False),
        active_school,
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        base_qs = base_qs.filter(
            Q(title__icontains=q)
            | Q(id__icontains=q)
            | Q(assignee__name__icontains=q)
            | Q(recipients__name__icontains=q)
        ).distinct()

    counts = dict(base_qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": counts.get("open", 0),
        "in_progress": counts.get("in_progress", 0),
        "done": counts.get("done", 0),
        "rejected": counts.get("rejected", 0),
    }

    status = request.GET.get("status")
    qs = base_qs
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")

    return render(
        request,
        "reports/my_requests.html",
        {"tickets": page, "page_obj": page, "stats": stats, "view_mode": view_mode},
    )

@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def ticket_detail(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    user = request.user

    # احضر التذكرة مع الحقول المطلوبة مع احترام المدرسة النشطة
    base_qs = Ticket.objects.select_related("creator", "assignee", "department").prefetch_related("recipients").only(
        "id", "title", "body", "status", "department", "created_at",
        "creator__name", "assignee__name", "assignee_id", "creator_id", "is_platform", "school_id"
    )
    
    # إذا كانت التذكرة للمنصة، لا نفلتر بالمدرسة (لأنها قد لا تكون مرتبطة بمدرسة أو نريد السماح للمدير برؤيتها)
    # لكن يجب التأكد أن المستخدم هو المنشئ أو مشرف نظام
    # سنحاول جلب التذكرة أولاً بدون فلتر المدرسة إذا كانت is_platform=True
    
    # الحل الأبسط: نعدل _filter_by_school ليتجاهل الفلتر إذا كانت التذكرة is_platform=True
    # لكن _filter_by_school تعمل على QuerySet.
    
    # لذا سنقوم بالتالي:
    # 1. نحاول جلب التذكرة بـ PK فقط
    # 2. نتحقق من الصلاحية يدوياً
    
    t = get_object_or_404(base_qs, pk=pk)
    
    # التحقق من الوصول
    if t.is_platform:
        # تذاكر المنصة: مسموحة للمنشئ (المدير) أو المشرف العام
        if not (user.is_superuser or t.creator_id == user.id):
             raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
    else:
        # تذاكر المدرسة: نلزم عضوية المستخدم في مدرسة التذكرة
        if not user.is_superuser:
            if not t.school_id:
                raise Http404("هذه التذكرة غير مرتبطة بمدرسة.")
            if is_platform_admin(user):
                if not platform_allowed_schools_qs(user).filter(id=t.school_id).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
            else:
                if not SchoolMembership.objects.filter(
                    teacher=user,
                    school_id=t.school_id,
                    is_active=True,
                ).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")

            # عند تعدد المدارس: نلزم توافق المدرسة النشطة مع مدرسة التذكرة
            if active_school is not None and t.school_id != active_school.id:
                raise Http404("هذه التذكرة تابعة لمدرسة أخرى.")

    is_owner = (t.creator_id == user.id)
    can_act = _can_act(user, t)

    if request.method == "POST":
        status_val = (request.POST.get("status") or "").strip()
        note_txt   = (request.POST.get("note") or "").strip()
        changed = False

        status_label = dict(getattr(Ticket.Status, "choices", [])).get

        locked_statuses = {Ticket.Status.DONE, Ticket.Status.REJECTED}
        is_locked_now_or_will_be = (t.status in locked_statuses) or (status_val in locked_statuses)

        # إضافة ملاحظة (المرسل أو من يملك الصلاحية)
        # يسمح للمرسل بإضافة ملاحظات (للتواصل) ولكن لا يملك صلاحية تغيير الحالة إلا إذا كان من ضمن المستلمين/الإدارة
        can_comment = False
        if is_owner or can_act:
            can_comment = True

        if note_txt and can_comment and is_locked_now_or_will_be:
            messages.warning(request, "لا يمكن إضافة ملاحظة عندما تكون حالة الطلب مكتمل أو مرفوض.")

        if note_txt and can_comment and (not is_locked_now_or_will_be):
            try:
                with transaction.atomic():
                    TicketNote.objects.create(
                        ticket=t, author=request.user, body=note_txt, is_public=True
                    )

                    # خيار: إعادة الفتح تلقائيًا عند ملاحظة المرسل (إن كانت مفعّلة)
                    if AUTO_REOPEN_ON_SENDER_NOTE and is_owner and t.status in {
                        Ticket.Status.DONE, Ticket.Status.REJECTED, Ticket.Status.IN_PROGRESS
                    }:
                        old_status = t.status
                        t.status = Ticket.Status.OPEN
                        try:
                            t.save(update_fields=["status"])
                        except Exception:
                            t.save()
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body=f"تغيير الحالة تلقائيًا بسبب ملاحظة المرسل: {status_label(old_status, old_status)} → {status_label(Ticket.Status.OPEN, Ticket.Status.OPEN)}",
                            is_public=True,
                        )
                changed = True
            except Exception:
                logger.exception("Failed to create note")
                messages.error(request, "تعذّر حفظ الملاحظة.")

        # تغيير الحالة (لمن له صلاحية فقط)
        if status_val:
            if not can_act:
                messages.warning(request, "لا يمكنك تغيير حالة هذا الطلب. يمكنك فقط إضافة ملاحظة.")
            else:
                valid_statuses = {k for k, _ in Ticket.Status.choices}
                if status_val in valid_statuses and status_val != t.status:
                    old = t.status
                    t.status = status_val
                    try:
                        t.save(update_fields=["status"])
                    except Exception:
                        t.save()
                    changed = True
                    try:
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body="تغيير الحالة: {} → {}".format(status_label(old, old), status_label(status_val, status_val)),
                            is_public=True,
                        )
                    except Exception:
                        logger.exception("Failed to create system note")

        if changed:
            messages.success(request, "تم حفظ التغييرات.")
        else:
            messages.info(request, "لا يوجد تغييرات.")
        return redirect("reports:ticket_detail", pk=pk)

    # ===== صور التذكرة (بغض النظر عن related_name) =====
    images_manager = getattr(t, "images", None)  # لو related_name='images'
    if images_manager is None:
        images_manager = getattr(t, "ticketimage_set", None)  # الاسم الافتراضي إن وُجد

    if images_manager is not None and hasattr(images_manager, "all"):
        images = list(images_manager.all().only("id", "image"))
    else:
        # fallback مضمون
        images = list(TicketImage.objects.filter(ticket_id=t.id).only("id", "image"))

    # سجلّ الملاحظات + نموذج الإجراء (إن وُجدت صلاحية)
    notes_qs = (
        t.notes.select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    form = TicketActionForm(initial={"status": t.status}) if can_act else None

    ctx = {
        "t": t,
        "images": images,     # ← استخدم هذا في القالب
        "notes": notes_qs,
        "form": form,
        "can_act": can_act,
        "is_owner": is_owner,
    }
    return render(request, "reports/ticket_detail.html", ctx)


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def ticket_note_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """تعديل ملاحظة طلب: فقط صاحب الملاحظة."""
    active_school = _get_active_school(request)
    user = request.user

    note = get_object_or_404(
        TicketNote.objects.select_related("ticket", "ticket__school", "author"),
        pk=pk,
    )
    t = note.ticket

    if note.author_id != user.id:
        raise Http404("ليس لديك صلاحية لتعديل هذه الملاحظة.")

    # لا نسمح بالتعديل بعد اكتمال/رفض الطلب
    if getattr(t, "status", None) in {Ticket.Status.DONE, Ticket.Status.REJECTED}:
        messages.warning(request, "لا يمكن تعديل الملاحظات بعد تحويل الطلب إلى مكتمل أو مرفوض.")
        return redirect("reports:ticket_detail", pk=t.id)

    # تحقق الوصول للتذكرة (نفس منطق ticket_detail)
    if getattr(t, "is_platform", False):
        if not (user.is_superuser or t.creator_id == user.id or note.author_id == user.id):
            raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
    else:
        if not user.is_superuser:
            if not getattr(t, "school_id", None):
                raise Http404("هذه التذكرة غير مرتبطة بمدرسة.")
            if is_platform_admin(user):
                if not platform_allowed_schools_qs(user).filter(id=t.school_id).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
            else:
                if not SchoolMembership.objects.filter(teacher=user, school_id=t.school_id, is_active=True).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
            if active_school is not None and t.school_id != active_school.id:
                raise Http404("هذه التذكرة تابعة لمدرسة أخرى.")

    next_url = (request.GET.get("next") or "").strip()
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ""

    if request.method == "POST":
        form = TicketNoteEditForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الملاحظة.")
            return redirect(next_url or "reports:ticket_detail", pk=t.id)
    else:
        form = TicketNoteEditForm(instance=note)

    return render(
        request,
        "reports/ticket_note_edit.html",
        {"t": t, "note": note, "form": form, "next": next_url or ""},
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def ticket_print(request: HttpRequest, pk: int) -> HttpResponse:
    """طباعة رسمية للطلب (A4) بنفس أسلوب طباعة التقارير."""
    active_school = _get_active_school(request)
    user = request.user

    base_qs = Ticket.objects.select_related("creator", "assignee", "department", "school").prefetch_related("recipients").only(
        "id",
        "title",
        "body",
        "status",
        "department",
        "created_at",
        "creator__name",
        "assignee__name",
        "assignee_id",
        "creator_id",
        "is_platform",
        "school_id",
        "attachment",
        "school__name",
        "school__stage",
    )

    t = get_object_or_404(base_qs, pk=pk)

    # نفس منطق الصلاحيات في ticket_detail
    if t.is_platform:
        if not (user.is_superuser or t.creator_id == user.id):
            raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
    else:
        if not user.is_superuser:
            if not t.school_id:
                raise Http404("هذه التذكرة غير مرتبطة بمدرسة.")
            if is_platform_admin(user):
                if not platform_allowed_schools_qs(user).filter(id=t.school_id).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
            else:
                if not SchoolMembership.objects.filter(
                    teacher=user,
                    school_id=t.school_id,
                    is_active=True,
                ).exists():
                    raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")

            if active_school is not None and t.school_id != active_school.id:
                raise Http404("هذه التذكرة تابعة لمدرسة أخرى.")

    # المرفقات/الصور
    images_manager = getattr(t, "images", None)
    if images_manager is None:
        images_manager = getattr(t, "ticketimage_set", None)
    if images_manager is not None and hasattr(images_manager, "all"):
        images = list(images_manager.all().only("id", "image"))
    else:
        images = list(TicketImage.objects.filter(ticket_id=t.id).only("id", "image"))

    notes_qs = (
        t.notes.select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )

    # إعدادات المدرسة/الشعارات مثل report_print
    school_scope = getattr(t, "school", None) or active_school
    school_name = getattr(school_scope, "name", "") if school_scope else getattr(settings, "SCHOOL_NAME", "منصة التقارير المدرسية")
    school_stage = ""
    school_logo_url = ""
    if school_scope:
        try:
            school_stage = getattr(school_scope, "get_stage_display", lambda: "")() or ""
        except Exception:
            school_stage = getattr(school_scope, "stage", "") or ""
        # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
        school_logo_url = ""

    moe_logo_url = (getattr(settings, "MOE_LOGO_URL", "") or "").strip()
    if not moe_logo_url:
        try:
            moe_logo_static_path = (getattr(settings, "MOE_LOGO_STATIC", "") or "").strip()
            if moe_logo_static_path:
                moe_logo_url = static(moe_logo_static_path)
        except Exception:
            moe_logo_url = ""
    if not moe_logo_url:
        moe_logo_url = static("img/UntiTtled-1.png")

    # خصائص المرفق الرئيسي
    attachment_name_lower = (getattr(getattr(t, "attachment", None), "name", "") or "").lower()
    attachment_is_image = attachment_name_lower.endswith((".jpg", ".jpeg", ".png", ".webp"))
    attachment_is_pdf = attachment_name_lower.endswith(".pdf")

    now_local = timezone.localtime(timezone.now())

    return render(
        request,
        "reports/ticket_print.html",
        {
            "t": t,
            "notes": notes_qs,
            "images": images,
            "now": now_local,
            "SCHOOL_NAME": school_name,
            "SCHOOL_STAGE": school_stage,
            "SCHOOL_LOGO_URL": school_logo_url,
            "MOE_LOGO_URL": moe_logo_url,
            "attachment_is_image": attachment_is_image,
            "attachment_is_pdf": attachment_is_pdf,
        },
    )

@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def admin_request_update(request: HttpRequest, pk: int) -> HttpResponse:
    return ticket_detail(request, pk)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def tickets_inbox(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")
    qs = Ticket.objects.select_related("creator", "assignee", "department").prefetch_related("recipients").order_by("-created_at")
    qs = _filter_by_school(qs, active_school)
    
    # استبعاد تذاكر الدعم الفني للمنصة (لأنها خاصة بالإدارة العليا)
    qs = qs.filter(is_platform=False)

    is_manager = _is_manager_in_school(request.user, active_school)
    if not is_manager:
        user_codes = _user_department_codes(request.user, active_school)
        qs = qs.filter(Q(assignee=request.user) | Q(recipients=request.user) | Q(department__slug__in=user_codes)).distinct()

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    mine = request.GET.get("mine") == "1"

    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(Q(assignee=request.user) | Q(recipients=request.user)).distinct()
    if q:
        for kw in q.split():
            qs = qs.filter(Q(title__icontains=kw) | Q(body__icontains=kw))

    ctx = {
        "tickets": qs[:200],
        "status": status,
        "q": q,
        "mine": mine,
        "status_choices": Ticket.Status.choices,
    }
    return render(request, "reports/tickets_inbox.html", ctx)

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def assigned_to_me(request: HttpRequest) -> HttpResponse:
    user = request.user
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    user_codes = _user_department_codes(user, active_school)

    qs = Ticket.objects.select_related("creator", "assignee", "department").prefetch_related("recipients").filter(
        Q(assignee=user)
        | Q(recipients=user)
        | Q(assignee__isnull=True, department__slug__in=user_codes)
    ).distinct()
    qs = _filter_by_school(qs, active_school)
    
    # استبعاد تذاكر الدعم الفني للمنصة
    qs = qs.filter(is_platform=False)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(creator__name__icontains=q) | Q(id__icontains=q))

    status = request.GET.get("status")
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    raw_counts = dict(qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": raw_counts.get("open", 0),
        "in_progress": raw_counts.get("in_progress", 0),
        "done": raw_counts.get("done", 0),
        "rejected": raw_counts.get("rejected", 0),
    }

    page_obj = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")

    return render(request, "reports/assigned_to_me.html", {"page_obj": page_obj, "stats": stats, "view_mode": view_mode})
