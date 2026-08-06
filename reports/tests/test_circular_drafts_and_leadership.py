# -*- coding: utf-8 -*-
"""مسودات التعاميم، وإصلاح الاعتماد الذاتي في ملف الأداء القيادي.

خاصيتان:

1. **الاعتماد هو النشر.** مسودةٌ معتمَدة لم تُنشر تترك الجميع يظنها وصلت —
   وهي أسوأ حالة تقع فيها منظومة تعاميم.
2. **لا يعتمد المدير ملف أدائه.** خرقٌ رُصد في أول تحليل للمشروع، ويُصلَح هنا
   بالتمييز نفسه الذي بُني للمحاضر: مدرسةٌ في مجموعة يعتمده مديرها التنفيذي،
   ومدرسةٌ مستقلة يُصدره مديرها — فالقاعدة تُطبَّق حيث لها معنى ولا تُعطِّل
   ملفاً لا مراجع له.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from reports import capabilities as caps
from reports.model_parts.approvals import ApprovalState
from reports.models import (
    CircularDraft,
    Department,
    DepartmentMembership,
    LeadershipPortfolioSection,
    Notification,
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolLeadershipPortfolio,
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
    issue,
    submit,
)
from reports.services_circular_drafts import draft_recipients, publish_draft


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


def _plan():
    return SubscriptionPlan.objects.create(
        name="باقة", price=0, days_duration=365, max_teachers=0
    )


class CircularDraftBase(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التعاميم", code="cd-school")
        SchoolSubscription.objects.create(school=self.school, plan=_plan())

        self.manager = _user("المدير", "0500090001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.staff = _user("الموظف", "0500090002")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.staff, role_type=SchoolMembership.RoleType.ADMIN_STAFF
        )
        self.teacher = _user("معلم", "0500090003")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.department = Department.objects.create(
            school=self.school, name="الإدارية", slug="cd-ops"
        )
        DepartmentMembership.objects.create(department=self.department, teacher=self.staff)

        membership = SchoolMembership.objects.get(school=self.school, teacher=self.staff)
        scope = StaffScope.objects.create(
            membership=membership, capabilities=[caps.DRAFT_CIRCULARS]
        )
        scope.departments.add(self.department)

    def _draft(self, owner=None, **overrides):
        data = {
            "school": self.school,
            "owner": owner or self.staff,
            "title": "تعميم دوام الاختبارات",
            "body": "يبدأ الدوام الساعة السابعة خلال فترة الاختبارات.",
        }
        data.update(overrides)
        return CircularDraft.objects.create(**data)


class CircularDraftFlowTests(CircularDraftBase):
    def test_a_draft_starts_as_a_draft(self):
        draft = self._draft()
        self.assertEqual(draft.approval_state, ApprovalState.DRAFT)
        self.assertFalse(draft.is_published)

    def test_an_empty_draft_cannot_be_submitted(self):
        draft = self._draft(body="   ")
        with self.assertRaises(ValidationError):
            submit(draft, self.staff, school=self.school)

    def test_a_department_audience_needs_a_department(self):
        draft = self._draft(audience=CircularDraft.Audience.DEPARTMENT)
        with self.assertRaises(ValidationError):
            submit(draft, self.staff, school=self.school)

    def test_the_staff_submits_and_the_manager_approves(self):
        draft = self._draft()
        submit(draft, self.staff, school=self.school)
        self.assertEqual(draft.approval_state, ApprovalState.SUBMITTED)

        approve(draft, self.manager, school=self.school)
        self.assertEqual(draft.approval_state, ApprovalState.APPROVED)

    def test_the_author_cannot_approve_their_own_draft(self):
        """التوصيف صريح: لا ينشر الوكيل ولا الموظف تعميماً دون اعتماد المدير."""
        draft = self._draft()
        submit(draft, self.staff, school=self.school)

        with self.assertRaises((PermissionDenied, ApprovalError)):
            approve(draft, self.staff, school=self.school)

        draft.refresh_from_db()
        self.assertNotEqual(draft.approval_state, ApprovalState.APPROVED)

    def test_a_deputy_cannot_approve_a_draft_even_within_scope(self):
        """اعتماد المسودة بيد المدير وحده — ولا مدخل للنطاق فيه."""
        deputy = _user("الوكيل", "0500090010")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        scope = StaffScope.objects.create(
            membership=membership,
            capabilities=[caps.DRAFT_CIRCULARS, caps.REVIEW_REPORTS, caps.RECOMMEND_APPROVAL],
        )
        scope.departments.add(self.department)

        draft = self._draft()
        submit(draft, self.staff, school=self.school)

        self.assertEqual(available_actions(draft, deputy, school=self.school), [])

    def test_the_manager_issues_their_own_draft_directly(self):
        """المدير صاحب المسودة وصاحب سلطتها — فيُصدرها ولا ينتظر مراجعاً."""
        draft = self._draft(owner=self.manager)
        actions = available_actions(draft, self.manager, school=self.school)

        self.assertIn("issue", actions)
        issue(draft, self.manager, school=self.school)
        self.assertEqual(draft.approval_state, ApprovalState.APPROVED)


class CircularPublishingTests(CircularDraftBase):
    def test_publishing_creates_a_real_notification_with_recipients(self):
        draft = self._draft()
        submit(draft, self.staff, school=self.school)
        approve(draft, self.manager, school=self.school)

        notification = publish_draft(draft, self.manager)

        self.assertIsInstance(notification, Notification)
        self.assertEqual(notification.school_id, self.school.pk)
        self.assertEqual(notification.title, draft.title)
        self.assertTrue(notification.requires_signature)
        # الموظف والمعلم. والمدير خارجهم: هو مُصدر التعميم لا مخاطَبٌ به،
        # وإدراجُه يطالبه بالتوقيع على ما أصدره.
        self.assertEqual(notification.recipients.count(), 2)
        self.assertNotIn(
            self.manager.pk,
            list(notification.recipients.values_list("teacher_id", flat=True)),
        )

    def test_the_publisher_is_recorded_as_the_author(self):
        """التعميم يصدر باسم من يملك إصداره لا باسم من صاغه."""
        draft = self._draft()
        submit(draft, self.staff, school=self.school)
        approve(draft, self.manager, school=self.school)
        notification = publish_draft(draft, self.manager)

        self.assertEqual(notification.created_by_id, self.manager.pk)

    def test_a_department_draft_reaches_that_department_only(self):
        draft = self._draft(
            audience=CircularDraft.Audience.DEPARTMENT, department=self.department
        )
        self.assertEqual(draft_recipients(draft), [self.staff.pk])

    def test_publishing_twice_returns_the_same_notification(self):
        """مسودةٌ تُنشر مرتين تصير لها نسختا تواقيع فلا يُعرف أيهما الحجّة."""
        draft = self._draft()
        submit(draft, self.staff, school=self.school)
        approve(draft, self.manager, school=self.school)

        first = publish_draft(draft, self.manager)
        second = publish_draft(draft, self.manager)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Notification.objects.filter(school=self.school).count(), 1)

    def test_the_untouched_notification_path_still_works(self):
        """المسودات لا تلوّث جدول التعاميم — فهي نموذج مستقل."""
        Notification.objects.create(school=self.school, title="تعميم مباشر", message="نص")
        self._draft()

        self.assertEqual(Notification.objects.filter(school=self.school).count(), 1)
        self.assertEqual(CircularDraft.objects.filter(school=self.school).count(), 1)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CircularDraftScreenTests(CircularDraftBase):
    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_a_plain_teacher_cannot_open_the_screen(self):
        self._enter(self.teacher)
        response = self.client.get(reverse("reports:circular_draft_list"))
        self.assertEqual(response.status_code, 302)

    def test_the_staff_with_the_capability_opens_it(self):
        self._enter(self.staff)
        response = self.client.get(reverse("reports:circular_draft_list"))
        self.assertEqual(response.status_code, 200)

    def test_a_colleague_does_not_see_another_persons_draft(self):
        """ما لم يُقرَّر لا يبدو مقرَّراً."""
        self._draft(title="مسودة الموظف")
        other = _user("موظف آخر", "0500090020")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=other, role_type=SchoolMembership.RoleType.ADMIN_STAFF
        )
        StaffScope.objects.create(membership=membership, capabilities=[caps.DRAFT_CIRCULARS])
        self._enter(other)

        response = self.client.get(reverse("reports:circular_draft_list"))
        self.assertNotContains(response, "مسودة الموظف")

    def test_approving_from_the_screen_publishes_immediately(self):
        """لا خطوة ثالثة بعد الاعتماد."""
        draft = self._draft()
        submit(draft, self.staff, school=self.school)
        self._enter(self.manager)

        self.client.post(
            reverse("reports:circular_draft_action", args=[draft.pk]),
            {"approval_action": "approve", "note": "معتمد"},
        )
        draft.refresh_from_db()

        self.assertEqual(draft.approval_state, ApprovalState.APPROVED)
        self.assertTrue(draft.is_published)
        self.assertIsNotNone(draft.published_notification_id)


class LeadershipSelfApprovalTests(TestCase):
    """الخرق الذي رُصد في أول تحليل — ويُصلَح هنا."""

    def setUp(self):
        self.group = SchoolGroup.objects.create(name="مجمع", code="lp-group")

        self.grouped = School.objects.create(
            name="مدرسة ضمن مجموعة", code="lp-in", group=self.group
        )
        SchoolSubscription.objects.create(school=self.grouped, plan=_plan())
        self.grouped_manager = _user("مدير المجموعة", "0500091001")
        SchoolMembership.objects.create(
            school=self.grouped,
            teacher=self.grouped_manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.director = _user("المدير التنفيذي", "0500091002")
        SchoolGroupMembership.objects.create(group=self.group, user=self.director)

        self.solo = School.objects.create(name="مدرسة مستقلة", code="lp-solo")
        SchoolSubscription.objects.create(school=self.solo, plan=_plan())
        self.solo_manager = _user("مدير مستقل", "0500091003")
        SchoolMembership.objects.create(
            school=self.solo,
            teacher=self.solo_manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

    def _portfolio(self, school, manager):
        portfolio = SchoolLeadershipPortfolio.objects.create(
            school=school, manager=manager, academic_year="1447-1448"
        )
        LeadershipPortfolioSection.objects.create(
            portfolio=portfolio,
            code=LeadershipPortfolioSection.Code.PLANNING,
            is_completed=True,
        )
        return portfolio

    def test_a_grouped_manager_cannot_approve_their_own_portfolio(self):
        """القاعدة التي يكرّرها التوصيف في موضعين، وكانت مخروقة فعلياً."""
        portfolio = self._portfolio(self.grouped, self.grouped_manager)
        submit(portfolio, self.grouped_manager, school=self.grouped)

        with self.assertRaises((PermissionDenied, ApprovalError)):
            approve(portfolio, self.grouped_manager, school=self.grouped)

        portfolio.refresh_from_db()
        self.assertNotEqual(portfolio.approval_state, ApprovalState.APPROVED)

    def test_the_executive_director_approves_it(self):
        """بند صريح في توصيفه: متابعة ملفات الأداء القيادي لمديري مدارسه."""
        portfolio = self._portfolio(self.grouped, self.grouped_manager)
        submit(portfolio, self.grouped_manager, school=self.grouped)

        approve(portfolio, self.director, school=self.grouped)
        self.assertEqual(portfolio.approval_state, ApprovalState.APPROVED)
        self.assertEqual(portfolio.decided_by_id, self.director.pk)

    def test_the_executive_director_may_return_it_with_a_note(self):
        from reports.services_approval import return_for_changes

        portfolio = self._portfolio(self.grouped, self.grouped_manager)
        submit(portfolio, self.grouped_manager, school=self.grouped)
        return_for_changes(
            portfolio, self.director, school=self.grouped, note="أضف شواهد المحور الثالث"
        )

        self.assertEqual(portfolio.approval_state, ApprovalState.RETURNED)
        self.assertEqual(portfolio.review_note, "أضف شواهد المحور الثالث")

    def test_a_grouped_manager_is_offered_submit_not_issue(self):
        portfolio = self._portfolio(self.grouped, self.grouped_manager)
        actions = available_actions(portfolio, self.grouped_manager, school=self.grouped)

        self.assertIn("submit", actions)
        self.assertNotIn("issue", actions)

    def test_an_independent_manager_issues_their_portfolio(self):
        """لا سلطة فوقه فيها — وطلبُ مراجع يعني تعطيل الملف إلى الأبد."""
        portfolio = self._portfolio(self.solo, self.solo_manager)
        actions = available_actions(portfolio, self.solo_manager, school=self.solo)

        self.assertIn("issue", actions)
        self.assertNotIn("submit", actions)

        issue(portfolio, self.solo_manager, school=self.solo)
        self.assertEqual(portfolio.approval_state, ApprovalState.APPROVED)

    def test_an_empty_portfolio_cannot_be_sent(self):
        portfolio = SchoolLeadershipPortfolio.objects.create(
            school=self.grouped, manager=self.grouped_manager, academic_year="1447-1448"
        )
        with self.assertRaises(ValidationError):
            submit(portfolio, self.grouped_manager, school=self.grouped)

    def test_a_director_of_another_group_cannot_approve(self):
        other_group = SchoolGroup.objects.create(name="مجمع آخر", code="lp-other")
        stranger = _user("تنفيذي آخر", "0500091010")
        SchoolGroupMembership.objects.create(group=other_group, user=stranger)

        portfolio = self._portfolio(self.grouped, self.grouped_manager)
        submit(portfolio, self.grouped_manager, school=self.grouped)

        with self.assertRaises(PermissionDenied):
            approve(portfolio, stranger, school=self.grouped)
