# -*- coding: utf-8 -*-
"""تكليفات المدير التنفيذي على مدارس مجموعته.

القناة التي تفتحها هذه المرحلة كانت مفقودة تماماً: كان المدير التنفيذي يقرأ
إحصاءات ولا يملك سبيلاً لطلب عمل من مدارسه ولا لتلقّي ردّها.

والخاصية الحرجة هنا — كما في كل ما يخص هذا الدور — **ليست ما يستطيعه بل ما لا
يستطيعه**: يعتمد ما طلبه هو لأنه المكلِّف، ولا يكتسب بذلك أي صلاحية داخل
المدرسة ولا يصل إلى مجموعة غير مجموعته.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.model_parts.approvals import ApprovalState
from reports.models import (
    Assignment,
    AssignmentTarget,
    Report,
    ReportType,
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import is_school_manager
from reports.services_approval import ApprovalError, approve, submit


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


@override_settings(ALLOWED_HOSTS=["testserver"])
class GroupAssignmentBase(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="باقة المجموعة", price=0, days_duration=365, max_teachers=0
        )
        self.group = SchoolGroup.objects.create(name="مجمع النور", code="noor")
        self.other_group = SchoolGroup.objects.create(name="مجمع الفجر", code="fajr")

        self.director = _user("المدير التنفيذي", "0500040001")
        SchoolGroupMembership.objects.create(group=self.group, user=self.director)

        self.schools = []
        self.managers = []
        for index in range(2):
            school = School.objects.create(
                name=f"مدرسة {index}", code=f"g-school-{index}", group=self.group
            )
            SchoolSubscription.objects.create(school=school, plan=self.plan)
            manager = _user(f"مدير {index}", f"050004010{index}")
            SchoolMembership.objects.create(
                school=school,
                teacher=manager,
                role_type=SchoolMembership.RoleType.MANAGER,
            )
            self.schools.append(school)
            self.managers.append(manager)

        # مدرسة بلا مدير — الحالة التي يجب ألا تختفي بصمت.
        self.headless = School.objects.create(
            name="مدرسة بلا مدير", code="headless", group=self.group
        )
        SchoolSubscription.objects.create(school=self.headless, plan=self.plan)

    def _issue(self, schools=None, **overrides):
        data = {
            "scope": Assignment.Scope.GROUP,
            "group": self.group,
            "issuer": self.director,
            "title": "رفع خطة التحسين",
            "due_at": timezone.now() + timedelta(days=10),
        }
        data.update(overrides)
        assignment = Assignment.objects.create(**data)
        for index, school in enumerate(schools if schools is not None else self.schools):
            AssignmentTarget.objects.create(
                assignment=assignment,
                assignee=self.managers[index],
                school=school,
            )
        return assignment

    def _enter_director(self):
        self.client.force_login(self.director)

    def _enter_manager(self, index=0):
        self.client.force_login(self.managers[index])
        session = self.client.session
        session["active_school_id"] = self.schools[index].pk
        session.save()


class GroupAssignmentApprovalTests(GroupAssignmentBase):
    """الاعتماد يأتي من كونه المكلِّف — لا من صلاحية مدرسية."""

    def test_the_director_holds_no_school_membership(self):
        """الفرضية التي يقوم عليها كل ما بعدها، ويحرسها التنظيم."""
        for school in self.schools:
            self.assertFalse(is_school_manager(self.director, school))

    def test_the_director_can_approve_a_response_to_their_own_assignment(self):
        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)

        approve(target, self.director, school=target.school)
        self.assertEqual(target.approval_state, ApprovalState.APPROVED)
        self.assertEqual(target.decided_by_id, self.director.pk)

    def test_the_director_cannot_approve_a_report_inside_a_school(self):
        """الاعتماد الداخلي يبقى لمدير المدرسة — ولا يمتد إليه بحال."""
        school = self.schools[0]
        teacher = _user("معلم", "0500040020")
        SchoolMembership.objects.create(
            school=school, teacher=teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        category = ReportType.objects.create(school=school, code="x", name="نوع")
        report = Report.objects.create(
            school=school,
            teacher=teacher,
            title="تقرير داخلي",
            report_date=timezone.now().date(),
            category=category,
        )
        submit(report, teacher)

        with self.assertRaises(PermissionDenied):
            approve(report, self.director, school=school)

    def test_a_school_manager_cannot_approve_their_own_response(self):
        """القاعدة الكبرى تسري هنا أيضاً: لا يعتمد أحد عمله."""
        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)

        with self.assertRaises(ApprovalError):
            approve(target, target.assignee, school=target.school)

    def test_a_director_of_another_group_cannot_approve(self):
        stranger = _user("تنفيذي آخر", "0500040030")
        SchoolGroupMembership.objects.create(group=self.other_group, user=stranger)

        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)

        with self.assertRaises(PermissionDenied):
            approve(target, stranger, school=target.school)

    def test_the_school_manager_may_still_manage_their_school(self):
        """التكليف لا ينتقص من صلاحيات مدير المدرسة."""
        self.assertTrue(is_school_manager(self.managers[0], self.schools[0]))


class GroupAssignmentDeliveryTests(GroupAssignmentBase):
    """التكليف يصل المدير لا المدرسة — والمدرسة بلا مدير تُعلَن."""

    def test_a_target_is_created_per_school_with_its_manager(self):
        assignment = self._issue()
        pairs = set(assignment.targets.values_list("school_id", "assignee_id"))

        self.assertEqual(
            pairs,
            {
                (self.schools[0].pk, self.managers[0].pk),
                (self.schools[1].pk, self.managers[1].pk),
            },
        )

    def test_a_headless_school_is_reported_not_silently_dropped(self):
        from reports.forms_assignments import GroupAssignmentForm

        form = GroupAssignmentForm(
            {
                "title": "تكليف",
                "description": "",
                "priority": Assignment.Priority.NORMAL,
                "due_at": (timezone.localtime() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
                "min_evidence_count": 1,
                "schools": [self.schools[0].pk, self.headless.pk],
            },
            group=self.group,
            issuer=self.director,
            allowed_schools=School.objects.filter(group=self.group),
        )
        self.assertTrue(form.is_valid(), form.errors)

        recipients, unreachable = form.resolve_recipients()
        self.assertEqual([school.pk for school in unreachable], [self.headless.pk])
        self.assertEqual([manager.pk for manager, _ in recipients], [self.managers[0].pk])

    def test_the_manager_sees_the_group_assignment_in_their_own_list(self):
        """التكليف الصاعد من المجموعة يظهر في «تكليفاتي» بلا شاشة خاصة."""
        self._issue()
        self._enter_manager(0)

        response = self.client.get(reverse("reports:my_assignments"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "رفع خطة التحسين")

    def test_a_manager_of_another_school_does_not_see_it(self):
        self._issue(schools=[self.schools[0]])
        self._enter_manager(1)

        response = self.client.get(reverse("reports:my_assignments"))
        self.assertNotContains(response, "رفع خطة التحسين")


class GroupAssignmentScreenTests(GroupAssignmentBase):
    def test_a_school_manager_cannot_open_the_group_board(self):
        self._enter_manager(0)
        response = self.client.get(reverse("reports:group_assignment_board"))
        self.assertEqual(response.status_code, 404)

    def test_the_director_opens_the_board(self):
        self._issue()
        self._enter_director()

        response = self.client.get(reverse("reports:group_assignment_board"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "رفع خطة التحسين")

    def test_issuing_from_the_screen_reaches_every_managed_school(self):
        self._enter_director()
        due = timezone.localtime() + timedelta(days=6)

        response = self.client.post(
            reverse("reports:group_assignment_create"),
            {
                "group": self.group.pk,
                "title": "تقرير الفصل الأول",
                "description": "المطلوب مفصّل",
                "priority": Assignment.Priority.NORMAL,
                "due_at": due.strftime("%Y-%m-%dT%H:%M"),
                "min_evidence_count": 1,
                "schools": [self.schools[0].pk, self.schools[1].pk],
            },
        )
        self.assertEqual(response.status_code, 302)

        assignment = Assignment.objects.get(title="تقرير الفصل الأول")
        self.assertEqual(assignment.scope, Assignment.Scope.GROUP)
        self.assertEqual(assignment.group_id, self.group.pk)
        self.assertEqual(assignment.targets.count(), 2)

    def test_a_school_outside_the_group_is_refused(self):
        outside = School.objects.create(
            name="مدرسة خارجية", code="outsider", group=self.other_group
        )
        SchoolSubscription.objects.create(school=outside, plan=self.plan)
        self._enter_director()
        due = timezone.localtime() + timedelta(days=6)

        self.client.post(
            reverse("reports:group_assignment_create"),
            {
                "group": self.group.pk,
                "title": "تكليف متسلل",
                "priority": Assignment.Priority.NORMAL,
                "due_at": due.strftime("%Y-%m-%dT%H:%M"),
                "min_evidence_count": 1,
                "schools": [outside.pk],
            },
        )
        self.assertFalse(Assignment.objects.filter(title="تكليف متسلل").exists())

    def test_a_target_of_another_group_reads_as_missing(self):
        stranger = _user("تنفيذي آخر", "0500040040")
        SchoolGroupMembership.objects.create(group=self.other_group, user=stranger)
        assignment = self._issue()
        target = assignment.targets.first()

        self.client.force_login(stranger)
        response = self.client.get(
            reverse("reports:group_assignment_detail", args=[target.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_director_approves_a_response_through_the_screen(self):
        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)
        self._enter_director()

        self.client.post(
            reverse("reports:group_assignment_action", args=[target.pk]),
            {"approval_action": "approve", "note": "خطة متكاملة"},
        )
        target.refresh_from_db()
        self.assertEqual(target.approval_state, ApprovalState.APPROVED)

    def test_returning_a_response_requires_a_note(self):
        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)
        self._enter_director()

        self.client.post(
            reverse("reports:group_assignment_action", args=[target.pk]),
            {"approval_action": "return", "note": "   "},
        )
        target.refresh_from_db()
        self.assertEqual(target.approval_state, ApprovalState.SUBMITTED)

    def test_a_returned_response_goes_back_to_the_school(self):
        assignment = self._issue()
        target = assignment.targets.first()
        submit(target, target.assignee)
        self._enter_director()

        self.client.post(
            reverse("reports:group_assignment_action", args=[target.pk]),
            {"approval_action": "return", "note": "أضف مؤشرات القياس"},
        )
        target.refresh_from_db()

        self.assertEqual(target.approval_state, ApprovalState.RETURNED)
        self.assertTrue(target.is_editable_by_owner)
        self.assertEqual(target.review_note, "أضف مؤشرات القياس")

    def test_cancelling_keeps_the_assignment_visible(self):
        assignment = self._issue()
        self._enter_director()

        self.client.post(
            reverse("reports:group_assignment_cancel", args=[assignment.pk]),
            {"reason": "تغيّرت الأولويات"},
        )
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_cancelled)
        self.assertTrue(Assignment.objects.filter(pk=assignment.pk).exists())
