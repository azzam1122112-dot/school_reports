# -*- coding: utf-8 -*-
"""دورة اعتماد التقارير.

ما تحرسه هذه الاختبارات ثلاث قواعد لا تُستثنى، تتكرر في توصيف الأدوار الخمسة:

1. **لا يعتمد أحد عمله** — ولو كان مدير المدرسة.
2. **المعتمد نهائي** — لا يُعدَّل ولا يعود.
3. **الوكيل يوصي ولا يعتمد** — إلا في المسار الذي يفوّضه المدير صراحةً.

ويضاف إليها ما يخص الترقية: مدرسة لم تفعّل الدورة يجب أن تعمل بعد هذه المرحلة
كما كانت قبلها حرفياً.
"""
from __future__ import annotations

from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from reports import capabilities as caps
from reports.model_parts.approvals import ApprovalRoute, ApprovalState, ApprovalTransition
from reports.models import (
    Department,
    Report,
    ReportType,
    School,
    SchoolMembership,
    SchoolSubscription,
    StaffScope,
    SubscriptionPlan,
    Teacher,
)
from reports.services_approval import (
    ApprovalError,
    approve,
    available_actions,
    recommend,
    request_info,
    return_for_changes,
    start_review,
    submit,
    withdraw,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


def _school(name: str, code: str, *, approval: bool = True) -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=0
    )
    school = School.objects.create(name=name, code=code, report_approval_enabled=approval)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


class ApprovalFlowTests(TestCase):
    """المسار الكامل: مسودة ← إرسال ← مراجعة ← توصية ← اعتماد."""

    def setUp(self):
        self.school = _school("مدرسة الاعتماد", "approve-school")

        self.manager = _user("المدير", "0500020001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )

        self.department = Department.objects.create(
            school=self.school, name="الشؤون التعليمية", slug="academic"
        )
        self.category = ReportType.objects.create(
            school=self.school,
            code="lesson",
            name="درس تطبيقي",
            approval_route=ApprovalRoute.VIA_DEPUTY,
        )
        self.category.departments.add(self.department)

        self.deputy = _user("الوكيل", "0500020002")
        deputy_membership = SchoolMembership.objects.create(
            school=self.school, teacher=self.deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        scope = StaffScope.objects.create(
            membership=deputy_membership,
            capabilities=[caps.REVIEW_REPORTS, caps.RECOMMEND_APPROVAL],
        )
        scope.departments.add(self.department)

        self.teacher = _user("المعلم", "0500020003")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )

        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="حصة الرياضيات",
            report_date=date(2026, 8, 1),
            category=self.category,
        )

    # ── المسار السعيد ────────────────────────────────────────────────
    def test_a_new_report_starts_as_a_draft(self):
        self.assertEqual(self.report.approval_state, ApprovalState.DRAFT)
        self.assertTrue(self.report.is_editable_by_owner)

    def test_the_full_route_through_the_deputy(self):
        submit(self.report, self.teacher)
        self.assertEqual(self.report.approval_state, ApprovalState.SUBMITTED)

        start_review(self.report, self.deputy)
        self.assertEqual(self.report.approval_state, ApprovalState.UNDER_REVIEW)

        recommend(self.report, self.deputy, note="عمل متقن")
        self.assertEqual(self.report.approval_state, ApprovalState.RECOMMENDED)

        approve(self.report, self.manager)
        self.assertEqual(self.report.approval_state, ApprovalState.APPROVED)
        self.assertEqual(self.report.decided_by_id, self.manager.pk)
        self.assertEqual(self.report.reviewed_by_id, self.deputy.pk)

    def test_reviewer_and_approver_are_recorded_separately(self):
        """دمجُهما في حقل واحد يمحو أثر المراجعة الوسيطة — وهي بيت القصيد."""
        submit(self.report, self.teacher)
        recommend(self.report, self.deputy)
        approve(self.report, self.manager)

        self.assertNotEqual(self.report.reviewed_by_id, self.report.decided_by_id)

    def test_every_step_is_recorded_in_order(self):
        submit(self.report, self.teacher)
        start_review(self.report, self.deputy)
        recommend(self.report, self.deputy)
        approve(self.report, self.manager)

        actions = list(
            ApprovalTransition.objects.filter(object_id=self.report.pk)
            .order_by("created_at", "id")
            .values_list("action", flat=True)
        )
        self.assertEqual(actions, ["submit", "start_review", "recommend", "approve"])

    # ── قاعدة: لا يعتمد أحد عمله ─────────────────────────────────────
    def test_a_manager_cannot_approve_their_own_report(self):
        """القاعدة التي كانت مخروقة فعلياً في ملف الأداء القيادي."""
        own = Report.objects.create(
            school=self.school,
            teacher=self.manager,
            title="تقرير المدير",
            report_date=date(2026, 8, 2),
            category=self.category,
        )
        submit(own, self.manager)

        with self.assertRaises(ApprovalError):
            approve(own, self.manager)

    def test_a_deputy_cannot_recommend_their_own_report(self):
        own = Report.objects.create(
            school=self.school,
            teacher=self.deputy,
            title="تقرير الوكيل",
            report_date=date(2026, 8, 3),
            category=self.category,
        )
        submit(own, self.deputy)

        with self.assertRaises(ApprovalError):
            recommend(own, self.deputy)

    def test_a_manager_may_approve_a_deputys_report(self):
        own = Report.objects.create(
            school=self.school,
            teacher=self.deputy,
            title="تقرير الوكيل",
            report_date=date(2026, 8, 4),
            category=self.category,
        )
        submit(own, self.deputy)
        approve(own, self.manager)
        self.assertEqual(own.approval_state, ApprovalState.APPROVED)

    # ── قاعدة: الوكيل يوصي ولا يعتمد ────────────────────────────────
    def test_a_deputy_cannot_approve_on_the_default_route(self):
        self.category.approval_route = ApprovalRoute.DIRECT
        self.category.save(update_fields=["approval_route"])
        submit(self.report, self.teacher)
        with self.assertRaises(PermissionDenied):
            approve(self.report, self.deputy)

    def test_a_deputy_cannot_review_a_direct_manager_report(self):
        self.category.approval_route = ApprovalRoute.DIRECT
        self.category.save(update_fields=["approval_route"])
        submit(self.report, self.teacher)

        with self.assertRaises(PermissionDenied):
            start_review(self.report, self.deputy)

    def test_a_deputy_may_approve_when_the_route_says_so(self):
        """المسار الذي يفوّضه المدير صراحةً لنوع تقرير بعينه."""
        self.category.approval_route = ApprovalRoute.DEPUTY_FINAL
        self.category.save(update_fields=["approval_route"])

        submit(self.report, self.teacher)
        approve(self.report, self.deputy)
        self.assertEqual(self.report.approval_state, ApprovalState.APPROVED)

    # ── قاعدة: النطاق يحكم ──────────────────────────────────────────
    def test_a_deputy_cannot_review_outside_their_departments(self):
        """النطاق الفارغ يعني لا شيء، لا كل شيء."""
        other_category = ReportType.objects.create(
            school=self.school, code="admin", name="تقرير إداري"
        )
        outside = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="خارج النطاق",
            report_date=date(2026, 8, 5),
            category=other_category,
        )
        submit(outside, self.teacher)

        with self.assertRaises(PermissionDenied):
            start_review(outside, self.deputy)

    def test_a_plain_teacher_cannot_review_anything(self):
        peer = _user("زميل", "0500020004")
        SchoolMembership.objects.create(
            school=self.school, teacher=peer, role_type=SchoolMembership.RoleType.TEACHER
        )
        submit(self.report, self.teacher)

        with self.assertRaises(PermissionDenied):
            start_review(self.report, peer)

    # ── الإعادة والاستكمال ──────────────────────────────────────────
    def test_returning_requires_a_note(self):
        """إعادةٌ بلا ملاحظة تُرجع صاحبها حائراً."""
        submit(self.report, self.teacher)
        with self.assertRaises(ApprovalError):
            return_for_changes(self.report, self.deputy, note="   ")

    def test_requesting_info_requires_a_note(self):
        submit(self.report, self.teacher)
        with self.assertRaises(ApprovalError):
            request_info(self.report, self.deputy, note="")

    def test_a_returned_report_becomes_editable_again(self):
        submit(self.report, self.teacher)
        return_for_changes(self.report, self.deputy, note="أضف النتائج")

        self.assertEqual(self.report.approval_state, ApprovalState.RETURNED)
        self.assertTrue(self.report.is_editable_by_owner)
        self.assertEqual(self.report.review_note, "أضف النتائج")

    def test_a_returned_report_can_be_resubmitted(self):
        submit(self.report, self.teacher)
        return_for_changes(self.report, self.deputy, note="أضف النتائج")
        submit(self.report, self.teacher)

        self.assertEqual(self.report.approval_state, ApprovalState.SUBMITTED)

    def test_needs_info_is_distinct_from_returned(self):
        """رسالتان مختلفتان: «أعد النظر» غير «أرفق ما نقص»."""
        submit(self.report, self.teacher)
        request_info(self.report, self.deputy, note="أرفق صور النشاط")

        self.assertEqual(self.report.approval_state, ApprovalState.NEEDS_INFO)
        self.assertTrue(self.report.is_editable_by_owner)

    # ── قاعدة: المعتمد نهائي ────────────────────────────────────────
    def test_an_approved_report_cannot_be_resubmitted(self):
        submit(self.report, self.teacher)
        approve(self.report, self.manager)

        with self.assertRaises(ApprovalError):
            submit(self.report, self.teacher)

    def test_an_approved_report_cannot_be_returned(self):
        submit(self.report, self.teacher)
        approve(self.report, self.manager)

        with self.assertRaises(ApprovalError):
            return_for_changes(self.report, self.manager, note="تراجعت")

    def test_an_approved_report_is_not_editable_by_its_owner(self):
        submit(self.report, self.teacher)
        approve(self.report, self.manager)

        self.assertFalse(self.report.is_editable_by_owner)
        self.assertTrue(self.report.is_final)

    # ── السحب ───────────────────────────────────────────────────────
    def test_the_owner_may_withdraw_before_review_starts(self):
        submit(self.report, self.teacher)
        withdraw(self.report, self.teacher)
        self.assertEqual(self.report.approval_state, ApprovalState.DRAFT)

    def test_withdrawing_after_review_started_is_refused(self):
        """السحب من تحت يد المراجع يجعل مراجعته عبثاً."""
        submit(self.report, self.teacher)
        start_review(self.report, self.deputy)

        with self.assertRaises(ApprovalError):
            withdraw(self.report, self.teacher)

    def test_only_the_owner_may_submit(self):
        with self.assertRaises(ApprovalError):
            submit(self.report, self.deputy)

    # ── الإجراءات المتاحة ───────────────────────────────────────────
    def test_available_actions_match_what_the_service_allows(self):
        """القائمة التي تقرأ منها الشاشة يجب ألا تعرض ما ترفضه الخدمة."""
        submit(self.report, self.teacher)

        owner_actions = available_actions(self.report, self.teacher, school=self.school)
        self.assertEqual(owner_actions, ["withdraw"])

        deputy_actions = available_actions(self.report, self.deputy, school=self.school)
        self.assertIn("recommend", deputy_actions)
        self.assertNotIn("approve", deputy_actions)

        manager_actions = available_actions(self.report, self.manager, school=self.school)
        self.assertIn("approve", manager_actions)

    def test_an_approved_report_offers_no_actions_to_anyone(self):
        submit(self.report, self.teacher)
        approve(self.report, self.manager)

        for actor in (self.teacher, self.deputy, self.manager):
            self.assertEqual(available_actions(self.report, actor, school=self.school), [])


class DelegatedApprovalTests(TestCase):
    """الإجراء المُنفَّذ بتفويض يُنسب لمنفّذه وللمفوِّض معاً."""

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone

        from reports.models import Delegation

        self.school = _school("مدرسة النيابة", "proxy-school")
        self.manager = _user("المدير", "0500021001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.department = Department.objects.create(
            school=self.school, name="الإدارة", slug="admin-dept"
        )
        self.category = ReportType.objects.create(
            school=self.school,
            code="admin",
            name="تقرير إداري",
            approval_route=ApprovalRoute.VIA_DEPUTY,
        )
        self.category.departments.add(self.department)

        self.deputy = _user("الوكيل", "0500021002")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=self.deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        # لا نطاق دائم — الصلاحية كلها من التفويض، وهو ما يميز الحالة.
        scope = StaffScope.objects.create(
            membership=membership, capabilities=[caps.REVIEW_REPORTS]
        )
        scope.departments.add(self.department)

        Delegation.objects.create(
            school=self.school,
            delegator=self.manager,
            delegate=self.deputy,
            capabilities=[caps.RECOMMEND_APPROVAL],
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=2),
        )

        self.teacher = _user("موظف", "0500021003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="تقرير الصيانة",
            report_date=date(2026, 8, 6),
            category=self.category,
        )

    def test_a_delegated_action_is_marked_as_acting_by_proxy(self):
        submit(self.report, self.teacher)
        recommend(self.report, self.deputy, note="جاهز")

        step = ApprovalTransition.objects.filter(
            object_id=self.report.pk, action="recommend"
        ).first()

        self.assertTrue(step.by_proxy)
        self.assertEqual(step.on_behalf_of_id, self.manager.pk)

    def test_an_owned_capability_is_not_marked_as_proxy(self):
        """من يملكها أصالةً لا يُسجَّل عمله كأنه نيابة عن غيره."""
        submit(self.report, self.teacher)
        start_review(self.report, self.deputy)

        step = ApprovalTransition.objects.filter(
            object_id=self.report.pk, action="start_review"
        ).first()

        self.assertFalse(step.by_proxy)
        self.assertIsNone(step.on_behalf_of_id)


class TransitionImmutabilityTests(TestCase):
    def test_a_transition_cannot_be_edited(self):
        school = _school("مدرسة السجل", "trans-school")
        actor = _user("منفّذ", "0500022001")
        step = ApprovalTransition.objects.create(
            content_type_id=1,
            object_id=1,
            school=school,
            actor=actor,
            action=ApprovalTransition.Action.APPROVE,
        )
        step.note = "تعديل لاحق"
        # سجل الانتقالات يرفض التعديل بـ ``ValidationError`` صراحةً.
        with self.assertRaises(ValidationError):
            step.save()


class UpgradeSafetyTests(TestCase):
    """مدرسة لم تفعّل الدورة يجب أن تعمل كما كانت قبل هذه المرحلة."""

    def test_a_school_without_the_workflow_keeps_reports_final_on_save(self):
        school = _school("مدرسة تقليدية", "legacy-approve", approval=False)
        manager = _user("المدير", "0500023001")
        SchoolMembership.objects.create(
            school=school, teacher=manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.assertFalse(school.report_approval_enabled)

    def test_the_toggle_is_off_by_default(self):
        """ترقية المنصة لا يجوز أن تُخفي عمل كل معلّم خلف موافقة لم يطلبها أحد."""
        school = School.objects.create(name="مدرسة جديدة", code="fresh-school")
        self.assertFalse(school.report_approval_enabled)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ApprovalInboxTests(TestCase):
    """صندوق واحد للوكيل والمدير — والفرق يخرج من الإجراءات لا من الشاشة."""

    def setUp(self):
        self.school = _school("مدرسة الصندوق", "inbox-school")
        self.manager = _user("المدير", "0500024001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.department = Department.objects.create(
            school=self.school, name="التعليمية", slug="edu"
        )
        self.category = ReportType.objects.create(
            school=self.school,
            code="edu",
            name="تقرير تعليمي",
            approval_route=ApprovalRoute.VIA_DEPUTY,
        )
        self.category.departments.add(self.department)

        self.deputy = _user("الوكيل", "0500024002")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=self.deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        scope = StaffScope.objects.create(
            membership=membership,
            capabilities=[caps.REVIEW_REPORTS, caps.RECOMMEND_APPROVAL],
        )
        scope.departments.add(self.department)

        self.teacher = _user("المعلم", "0500024003")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )

        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="تقرير في النطاق",
            report_date=date(2026, 8, 7),
            category=self.category,
        )
        submit(self.report, self.teacher)

        self.outside = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="تقرير خارج النطاق",
            report_date=date(2026, 8, 8),
            category=ReportType.objects.create(
                school=self.school,
                code="other",
                name="نوع آخر",
                approval_route=ApprovalRoute.VIA_DEPUTY,
            ),
        )
        submit(self.outside, self.teacher)

        self.url = reverse("reports:approval_inbox")

    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_the_manager_sees_everything_pending(self):
        self._enter(self.manager)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقرير في النطاق")
        self.assertContains(response, "تقرير خارج النطاق")

    def test_the_deputy_sees_only_their_scope(self):
        self._enter(self.deputy)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقرير في النطاق")
        self.assertNotContains(response, "تقرير خارج النطاق")
        self.assertContains(response, "يتطلب إجراءً منك")
        self.assertContains(response, "دورك الآن")
        self.assertNotContains(response, "ينتهي القرار عندك")

    def test_a_direct_manager_report_never_appears_in_the_deputy_inbox(self):
        direct_category = ReportType.objects.create(
            school=self.school,
            code="direct-only",
            name="تقرير مباشر للمدير",
            approval_route=ApprovalRoute.DIRECT,
        )
        direct_category.departments.add(self.department)
        direct_report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="لا يظهر للوكيل",
            report_date=date(2026, 8, 9),
            category=direct_category,
        )
        submit(direct_report, self.teacher)
        self._enter(self.deputy)

        response = self.client.get(self.url)

        self.assertNotContains(response, "لا يظهر للوكيل")
        self.assertEqual(
            self.client.get(
                reverse("reports:approval_detail", args=[direct_report.pk])
            ).status_code,
            404,
        )

    def test_manager_can_configure_and_see_the_approval_route(self):
        self._enter(self.manager)
        edit_url = reverse("reports:reporttype_update", args=[self.category.pk])
        receiving_department = Department.objects.create(
            school=self.school, name="قسم الاستلام", slug="receiving"
        )

        response = self.client.get(edit_url)
        self.assertContains(response, 'name="approval_route"', html=False)
        self.assertContains(response, "عبر الوكيل ثم مدير المدرسة")
        self.assertContains(response, "الوكيل يعتمد نهائياً")

        response = self.client.post(
            edit_url,
            {
                "name": self.category.name,
                "description": "",
                "approval_route": ApprovalRoute.DEPUTY_FINAL,
                "departments": [str(receiving_department.pk)],
                "order": "0",
                "is_active": "on",
            },
        )
        self.assertRedirects(
            response,
            reverse("reports:reporttypes_list"),
            fetch_redirect_response=False,
        )
        self.category.refresh_from_db()
        self.assertEqual(self.category.approval_route, ApprovalRoute.DEPUTY_FINAL)
        self.assertEqual(
            list(self.category.departments.values_list("pk", flat=True)),
            [receiving_department.pk],
        )

        listing = self.client.get(reverse("reports:reporttypes_list"))
        self.assertContains(listing, "الوكيل يعتمد نهائياً")
        self.assertContains(listing, receiving_department.name)

    def test_deputy_route_requires_at_least_one_receiving_department(self):
        self._enter(self.manager)
        response = self.client.post(
            reverse("reports:reporttype_update", args=[self.category.pk]),
            {
                "name": self.category.name,
                "description": "",
                "approval_route": ApprovalRoute.VIA_DEPUTY,
                "order": "0",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "اختر قسمًا واحدًا على الأقل حتى يعرف النظام أي وكيل يستلم التقرير",
        )

    def test_a_plain_teacher_is_refused_the_inbox(self):
        self._enter(self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_a_report_outside_the_scope_reads_as_missing_not_forbidden(self):
        """تمييز «ممنوع» عن «غير موجود» يكشف وجود ما لا يحق له معرفته."""
        self._enter(self.deputy)
        response = self.client.get(
            reverse("reports:approval_detail", args=[self.outside.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_owner_can_open_their_own_report(self):
        self._enter(self.teacher)
        response = self.client.get(
            reverse("reports:approval_detail", args=[self.report.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_the_manager_approves_from_the_inbox(self):
        self._enter(self.manager)
        response = self.client.post(
            reverse("reports:approval_action", args=[self.report.pk]),
            {"approval_action": "approve", "next": "inbox"},
        )
        self.assertEqual(response.status_code, 302)

        self.report.refresh_from_db()
        self.assertEqual(self.report.approval_state, ApprovalState.APPROVED)

    def test_a_deputy_posting_approve_is_refused(self):
        """الزر غير معروض — والطلب المُصاغ يدوياً يُرفض أيضاً."""
        self._enter(self.deputy)
        self.client.post(
            reverse("reports:approval_action", args=[self.report.pk]),
            {"approval_action": "approve"},
        )
        self.report.refresh_from_db()
        self.assertNotEqual(self.report.approval_state, ApprovalState.APPROVED)

    def test_an_unknown_action_name_is_refused(self):
        self._enter(self.manager)
        self.client.post(
            reverse("reports:approval_action", args=[self.report.pk]),
            {"approval_action": "delete_everything"},
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.approval_state, ApprovalState.SUBMITTED)

    def test_the_detail_page_shows_the_approval_timeline(self):
        start_review(self.report, self.deputy)
        recommend(self.report, self.deputy, note="عمل جيد")
        self._enter(self.manager)

        response = self.client.get(
            reverse("reports:approval_detail", args=[self.report.pk])
        )
        self.assertContains(response, "تاريخ الاعتماد")
        self.assertContains(response, "عمل جيد")


@override_settings(ALLOWED_HOSTS=["testserver"])
class OwnerLockTests(TestCase):
    """القاعدة الثانية — «المعتمد نهائي» — مطبَّقةً على مُعِدّ التقرير.

    كانت القاعدة محروسةً في الخدمة وحدها: ``available_actions`` لا تعرض على
    صاحب المعتمَد شيئاً. لكن ``edit_my_report`` و``delete_my_report`` لا تمرّان
    بالخدمة، وكان ``can_edit_report`` يفحص الدور والملكية بلا نظرٍ إلى الحالة —
    فالمعلّم يعدّل نصّ تقريره المعتمَد والختمُ قائم فوقه، ويرمي المرسَل في
    السلة من تحت مراجعه.
    """

    def setUp(self):
        self.school = _school("مدرسة القفل", "lock-school")
        self.manager = _user("المدير", "0500025001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.teacher = _user("المعلم", "0500025002")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.category = ReportType.objects.create(
            school=self.school, code="visit", name="زيارة صفية"
        )

    def _report(self, state: str, *, owner=None) -> Report:
        return Report.objects.create(
            school=self.school,
            teacher=owner or self.teacher,
            title="زيارة",
            report_date=date(2026, 8, 1),
            category=self.category,
            approval_state=state,
        )

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    # ── ما يبقى في يد صاحبه ──────────────────────────────────────────
    def test_the_owner_still_edits_and_deletes_a_draft(self):
        from reports.permissions import can_delete_report, can_edit_report

        for state in (ApprovalState.DRAFT, ApprovalState.RETURNED, ApprovalState.NEEDS_INFO):
            report = self._report(state)
            self.assertTrue(
                can_edit_report(self.teacher, report, active_school=self.school), state
            )
            self.assertTrue(
                can_delete_report(self.teacher, report, active_school=self.school), state
            )

    # ── ما خرج من يده ────────────────────────────────────────────────
    def test_the_owner_cannot_edit_a_submitted_or_approved_report(self):
        from reports.permissions import can_edit_report

        for state in (
            ApprovalState.SUBMITTED,
            ApprovalState.UNDER_REVIEW,
            ApprovalState.RECOMMENDED,
            ApprovalState.APPROVED,
        ):
            report = self._report(state)
            self.assertFalse(
                can_edit_report(self.teacher, report, active_school=self.school), state
            )

    def test_editing_an_approved_report_leaves_its_text_untouched(self):
        report = self._report(ApprovalState.APPROVED)
        self._login(self.teacher)

        response = self.client.post(
            reverse("reports:edit_my_report", args=[report.pk]),
            {
                "section_selection_enabled": "1",
                "title": "عنوان مدسوس بعد الختم",
                "report_date": "2026-08-01",
                "category": self.category.code,
                "show_details": "on",
                "idea": "نصٌّ بديل يكفي طوله لاجتياز التحقق من الحقول.",
                "evidence-TOTAL_FORMS": "0",
                "evidence-INITIAL_FORMS": "0",
                "evidence-MIN_NUM_FORMS": "0",
                "evidence-MAX_NUM_FORMS": "8",
            },
        )

        report.refresh_from_db()
        self.assertEqual(report.title, "زيارة")
        self.assertEqual(report.approval_state, ApprovalState.APPROVED)
        # يُردّ إلى تقاريره لا إلى شاشة إدارةٍ لا يراها.
        self.assertRedirects(response, reverse("reports:my_reports"))

    def test_the_owner_cannot_trash_an_approved_report(self):
        report = self._report(ApprovalState.APPROVED)
        self._login(self.teacher)

        self.client.post(reverse("reports:delete_my_report", args=[report.pk]))

        report.refresh_from_db()
        self.assertIsNone(report.trashed_at)

    def test_the_owner_cannot_trash_a_report_under_review(self):
        report = self._report(ApprovalState.SUBMITTED)
        self._login(self.teacher)

        self.client.post(reverse("reports:delete_my_report", args=[report.pk]))

        report.refresh_from_db()
        self.assertIsNone(report.trashed_at)

    # ── حدود القفل ───────────────────────────────────────────────────
    def test_the_manager_is_not_bound_by_a_seal_they_own(self):
        """من يملك فكّ الختم لا يقيّده الختم — وإلا مُنع المدير من تقريره هو."""
        from reports.permissions import can_delete_report, can_edit_report

        own = self._report(ApprovalState.APPROVED, owner=self.manager)
        theirs = self._report(ApprovalState.APPROVED)

        for report in (own, theirs):
            self.assertTrue(can_edit_report(self.manager, report, active_school=self.school))
            self.assertTrue(can_delete_report(self.manager, report, active_school=self.school))

    def test_a_school_without_the_workflow_is_untouched_by_the_lock(self):
        """تقاريرها تُحفظ ``approved`` فوراً، فقيدُها به يجمّدها لحظة حفظها."""
        from reports.permissions import can_delete_report, can_edit_report

        legacy = _school("مدرسة بلا دورة", "lock-legacy", approval=False)
        teacher = _user("معلم تقليدي", "0500025003")
        SchoolMembership.objects.create(
            school=legacy, teacher=teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        report = Report.objects.create(
            school=legacy,
            teacher=teacher,
            title="تقرير",
            report_date=date(2026, 8, 1),
            approval_state=ApprovalState.APPROVED,
        )

        self.assertTrue(can_edit_report(teacher, report, active_school=legacy))
        self.assertTrue(can_delete_report(teacher, report, active_school=legacy))


@override_settings(ALLOWED_HOSTS=["testserver"])
class OwnerSubmissionDoorTests(TestCase):
    """للمعلّم بابٌ يرسل منه مسودته.

    زرّ الإرسال يعيش في ``approval_detail``، وتبويب «الاعتماد» مخفيّ عمّن لا
    يراجع. فكان المعلّم في مدرسةٍ فعّلت الدورة يكتب تقريره فيُحفظ مسودةً ولا
    شاشةَ تقول له إنها لم تبلغ أحداً ولا كيف يرسلها — والمنصة تعدّه موثَّقاً.
    """

    def setUp(self):
        self.school = _school("مدرسة الباب", "door-school")
        self.teacher = _user("المعلم", "0500026001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.category = ReportType.objects.create(
            school=self.school, code="program", name="برنامج"
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="برنامج القراءة",
            report_date=date(2026, 8, 1),
            category=self.category,
        )
        self.client.force_login(self.teacher)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_my_reports_shows_the_state_and_a_way_to_submit(self):
        response = self.client.get(reverse("reports:my_reports"))

        self.assertContains(response, "مسودة")
        self.assertContains(response, reverse("reports:approval_detail", args=[self.report.pk]))

    def test_the_preview_offers_the_owner_a_send_button(self):
        response = self.client.get(reverse("reports:report_print", args=[self.report.pk]))

        self.assertContains(response, "إرسال للاعتماد")
        self.assertContains(response, reverse("reports:approval_detail", args=[self.report.pk]))

    def test_the_home_draft_counter_links_to_the_list(self):
        response = self.client.get(reverse("reports:home"))

        self.assertContains(response, "مسودة لم تُرسل بعد")
        self.assertContains(response, "th-note-link")

    def test_a_school_without_the_workflow_shows_no_state_noise(self):
        """وسمُ «معتمد» على كل صفّ في مدرسةٍ بلا مراجع يخبر عن لا شيء."""
        self.school.report_approval_enabled = False
        self.school.save(update_fields=["report_approval_enabled"])

        body = self.client.get(reverse("reports:my_reports")).content.decode("utf-8")
        body = body.split("</style>")[-1]

        self.assertNotIn('class="id-chip mr-state"', body)
        self.assertNotIn("mr-iconbtn mr-send", body)
        # وزرّا التعديل والحذف باقيان كما كانا قبل هذه المرحلة.
        self.assertIn(reverse("reports:edit_my_report", args=[self.report.pk]), body)
        self.assertIn(reverse("reports:delete_my_report", args=[self.report.pk]), body)
