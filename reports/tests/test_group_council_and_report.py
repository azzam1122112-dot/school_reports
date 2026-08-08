# -*- coding: utf-8 -*-
"""مجلس مجموعة المدارس، والتقرير التنفيذي المجمَّع.

يحرس هذا الملف ثلاث خصائص:

1. **الإصدار غير الاعتماد.** رئيس المجلس يكتب محضره ويصدره — ولا مراجع فوقه.
   والباب الذي يفتح له ذلك مقفلٌ على من يجمع صفتَي صاحب الوثيقة وصاحب سلطتها،
   فلا يتسرب منه موظف يعتمد تقريره لأنه كتبه.
2. **قرار المجلس يصل مدرسة المسؤول عنه** — لا يبقى نصاً في محضر.
3. **رقم واحد للصيغتين.** Excel و PDF يقرآن من لقطة واحدة، فلا يفترق رقم بين
   ملفين مستخرجين في الدقيقة نفسها.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.model_parts.approvals import ApprovalState
from reports.models import (
    Assignment,
    AssignmentTarget,
    Decision,
    Meeting,
    MeetingAttendee,
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
from reports.services_approval import (
    ApprovalError,
    approve,
    available_actions,
    issue,
    submit,
)
from reports.services_group_export import (
    build_group_snapshot,
    build_group_workbook_bytes,
)
from reports.services_meetings import (
    convert_decision_to_assignment,
    ensure_minutes,
    mark_held,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


@override_settings(ALLOWED_HOSTS=["testserver"])
class CouncilBase(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="باقة", price=0, days_duration=365, max_teachers=0
        )
        self.group = SchoolGroup.objects.create(name="مجمع النور", code="council-noor")
        self.other_group = SchoolGroup.objects.create(name="مجمع آخر", code="council-other")

        self.director = _user("المدير التنفيذي", "0500060001")
        SchoolGroupMembership.objects.create(group=self.group, user=self.director)

        self.schools, self.managers = [], []
        for index in range(2):
            school = School.objects.create(
                name=f"مدرسة {index}", code=f"c-school-{index}", group=self.group
            )
            SchoolSubscription.objects.create(school=school, plan=self.plan)
            manager = _user(f"مدير {index}", f"050006010{index}")
            SchoolMembership.objects.create(
                school=school, teacher=manager, role_type=SchoolMembership.RoleType.MANAGER
            )
            self.schools.append(school)
            self.managers.append(manager)

    def _council(self, **overrides):
        data = {
            "scope": Meeting.Scope.GROUP,
            "group": self.group,
            "organizer": self.director,
            "title": "الجلسة الأولى للمجلس",
            "scheduled_at": timezone.now() + timedelta(days=2),
        }
        data.update(overrides)
        meeting = Meeting.objects.create(**data)
        for manager in self.managers:
            MeetingAttendee.objects.create(meeting=meeting, person=manager)
        return meeting

    def _enter_director(self):
        self.client.force_login(self.director)


class CouncilMinutesIssuanceTests(CouncilBase):
    """الفرق بين الإصدار والاعتماد."""

    def _held_council(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        minutes = ensure_minutes(meeting, recorder=self.director)
        minutes.body = "نوقشت خطة الفصل الثاني."
        minutes.save(update_fields=["body"])
        return meeting, minutes

    def test_the_chair_issues_their_own_minutes(self):
        """لا مراجع فوق رئيس المجلس — وطلبُه يعني تعطيل المحضر إلى الأبد."""
        meeting, minutes = self._held_council()

        issue(minutes, self.director, school=None)
        self.assertEqual(minutes.approval_state, ApprovalState.APPROVED)
        self.assertEqual(minutes.decided_by_id, self.director.pk)

    def test_issue_is_offered_instead_of_submit(self):
        """عرض الاثنين معاً يجعله يختار طريقاً ينتهي بانتظار لا يأتي."""
        meeting, minutes = self._held_council()
        actions = available_actions(minutes, self.director, school=None)

        self.assertIn("issue", actions)
        self.assertNotIn("submit", actions)

    def test_the_issuance_is_recorded_as_such(self):
        meeting, minutes = self._held_council()
        issue(minutes, self.director, school=None)

        self.assertIn("إصدار", minutes.review_note)

    def test_an_empty_minutes_cannot_be_issued(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        minutes = ensure_minutes(meeting, recorder=self.director)

        # ``assert_ready_for_submission`` هي التي ترفض المحضر الفارغ.
        with self.assertRaises(ValidationError):
            issue(minutes, self.director, school=None)

    def test_a_report_author_cannot_issue_their_report(self):
        """الباب مقفل على من لا يجمع صفتَي صاحب الوثيقة وصاحب سلطتها."""
        school = self.schools[0]
        teacher = _user("معلم", "0500060020")
        SchoolMembership.objects.create(
            school=school, teacher=teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        category = ReportType.objects.create(school=school, code="c", name="نوع")
        report = Report.objects.create(
            school=school,
            teacher=teacher,
            title="تقرير",
            report_date=timezone.now().date(),
            category=category,
        )
        with self.assertRaises(PermissionDenied):
            issue(report, teacher, school=school)

    def test_a_manager_cannot_issue_their_own_report(self):
        """المدير صاحب سلطة، لكن التقرير يمر بالمراجعة لا بالإصدار."""
        school = self.schools[0]
        manager = self.managers[0]
        category = ReportType.objects.create(school=school, code="m", name="نوع")
        report = Report.objects.create(
            school=school,
            teacher=manager,
            title="تقرير المدير",
            report_date=timezone.now().date(),
            category=category,
        )
        with self.assertRaises(PermissionDenied):
            issue(report, manager, school=school)

    def test_minutes_written_by_another_person_follow_the_review_route(self):
        """حين يكتبه غير المنظّم تبقى قاعدة «لا يعتمد أحد عمله» عاملة."""
        meeting = self._council()
        mark_held(meeting, self.director)
        minutes = ensure_minutes(meeting, recorder=self.managers[0])
        minutes.body = "محضر كتبه أمين السر."
        minutes.save(update_fields=["body"])

        actions = available_actions(minutes, self.managers[0], school=None)
        self.assertIn("submit", actions)
        self.assertNotIn("issue", actions)

        submit(minutes, self.managers[0], school=None)
        approve(minutes, self.director, school=None)
        self.assertEqual(minutes.approval_state, ApprovalState.APPROVED)

    def test_the_recorder_still_cannot_approve_their_own_minutes(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        minutes = ensure_minutes(meeting, recorder=self.managers[0])
        minutes.body = "محضر"
        minutes.save(update_fields=["body"])
        submit(minutes, self.managers[0], school=None)

        with self.assertRaises(ApprovalError):
            approve(minutes, self.managers[0], school=None)


class CouncilDecisionTests(CouncilBase):
    """قرار المجلس يصل مدرسة المسؤول عنه."""

    def test_a_council_decision_becomes_an_assignment_on_the_managers_school(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        decision = Decision.objects.create(
            meeting=meeting,
            title="رفع خطة التحسين",
            responsible=self.managers[0],
            due_at=timezone.now() + timedelta(days=7),
        )

        assignment = convert_decision_to_assignment(decision, self.director)

        self.assertEqual(assignment.scope, Assignment.Scope.GROUP)
        self.assertEqual(assignment.group_id, self.group.pk)
        self.assertEqual(assignment.source, Assignment.Source.DECISION)

        target = assignment.targets.get()
        self.assertEqual(target.assignee_id, self.managers[0].pk)
        self.assertEqual(target.school_id, self.schools[0].pk)

    def test_the_manager_sees_the_council_decision_as_work(self):
        """القرار يصل صاحبه عملاً لا نصاً في محضر."""
        meeting = self._council()
        mark_held(meeting, self.director)
        decision = Decision.objects.create(
            meeting=meeting,
            title="تجهيز تقرير الفصل",
            responsible=self.managers[0],
            due_at=timezone.now() + timedelta(days=7),
        )
        convert_decision_to_assignment(decision, self.director)

        self.client.force_login(self.managers[0])
        session = self.client.session
        session["active_school_id"] = self.schools[0].pk
        session.save()

        response = self.client.get(reverse("reports:my_assignments"))
        self.assertContains(response, "تجهيز تقرير الفصل")

    def test_only_the_chair_converts(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        decision = Decision.objects.create(
            meeting=meeting,
            title="قرار",
            responsible=self.managers[0],
            due_at=timezone.now() + timedelta(days=7),
        )
        with self.assertRaises(PermissionDenied):
            convert_decision_to_assignment(decision, self.managers[0])


class CouncilScreenTests(CouncilBase):
    def test_a_school_manager_cannot_open_the_council(self):
        self.client.force_login(self.managers[0])
        response = self.client.get(reverse("reports:council_list"))
        self.assertEqual(response.status_code, 404)

    def test_the_director_opens_the_council_list(self):
        self._council()
        self._enter_director()

        response = self.client.get(reverse("reports:council_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الجلسة الأولى للمجلس")

    def test_creating_a_session_invites_every_school_manager(self):
        self._enter_director()
        when = timezone.localtime() + timedelta(days=5)

        response = self.client.post(
            reverse("reports:council_create"),
            {
                "group": self.group.pk,
                "title": "جلسة الخطة",
                "purpose": "مراجعة الخطط",
                "scheduled_at": when.strftime("%Y-%m-%dT%H:%M"),
                "location": "مقر الإدارة",
                "schools": [s.pk for s in self.schools],
            },
        )
        self.assertEqual(response.status_code, 302)

        meeting = Meeting.objects.get(title="جلسة الخطة")
        self.assertEqual(meeting.scope, Meeting.Scope.GROUP)
        self.assertEqual(meeting.attendees.count(), 2)

    def test_a_council_of_another_group_reads_as_missing(self):
        meeting = self._council()
        stranger = _user("تنفيذي آخر", "0500060030")
        SchoolGroupMembership.objects.create(group=self.other_group, user=stranger)

        self.client.force_login(stranger)
        response = self.client.get(reverse("reports:council_detail", args=[meeting.pk]))
        self.assertEqual(response.status_code, 404)

    def test_the_chair_issues_the_minutes_through_the_screen(self):
        meeting = self._council()
        mark_held(meeting, self.director)
        minutes = ensure_minutes(meeting, recorder=self.director)
        minutes.body = "محضر الجلسة"
        minutes.save(update_fields=["body"])
        self._enter_director()

        self.client.post(
            reverse("reports:council_minutes_action", args=[meeting.pk]),
            {"approval_action": "issue"},
        )
        minutes.refresh_from_db()
        self.assertEqual(minutes.approval_state, ApprovalState.APPROVED)


class GroupReportTests(CouncilBase):
    """التقرير المجمَّع: مصدر واحد للصيغتين."""

    def _seed(self):
        teacher = _user("معلم", "0500060040")
        SchoolMembership.objects.create(
            school=self.schools[0],
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        category = ReportType.objects.create(school=self.schools[0], code="r", name="نوع")
        Report.objects.create(
            school=self.schools[0],
            teacher=teacher,
            title="تقرير",
            report_date=timezone.now().date(),
            category=category,
            approval_state=ApprovalState.APPROVED,
        )
        assignment = Assignment.objects.create(
            scope=Assignment.Scope.GROUP,
            group=self.group,
            issuer=self.director,
            title="تكليف",
            due_at=timezone.now() + timedelta(days=5),
        )
        AssignmentTarget.objects.create(
            assignment=assignment, assignee=self.managers[0], school=self.schools[0]
        )
        return teacher

    def test_the_snapshot_covers_every_active_school(self):
        snapshot = build_group_snapshot(self.group)
        self.assertEqual(snapshot["totals"]["schools"], 2)
        self.assertEqual(len(snapshot["rows"]), 2)

    def test_the_snapshot_counts_reports_and_assignments(self):
        self._seed()
        snapshot = build_group_snapshot(self.group)

        self.assertEqual(snapshot["totals"]["reports_total"], 1)
        self.assertEqual(snapshot["totals"]["assignments"], 1)
        self.assertEqual(snapshot["totals"]["assignments_done"], 0)

    def test_ranking_puts_the_higher_completion_first(self):
        """المقارنة هي غرض التقرير، وجدولٌ مرتّب أبجدياً يخفيها."""
        first = Assignment.objects.create(
            scope=Assignment.Scope.GROUP,
            group=self.group,
            issuer=self.director,
            title="تكليف مكتمل",
            due_at=timezone.now() + timedelta(days=5),
        )
        done = AssignmentTarget.objects.create(
            assignment=first, assignee=self.managers[0], school=self.schools[0]
        )
        submit(done, self.managers[0], school=self.schools[0])
        approve(done, self.director, school=self.schools[0])

        second = Assignment.objects.create(
            scope=Assignment.Scope.GROUP,
            group=self.group,
            issuer=self.director,
            title="تكليف معلّق",
            due_at=timezone.now() + timedelta(days=5),
        )
        AssignmentTarget.objects.create(
            assignment=second, assignee=self.managers[1], school=self.schools[1]
        )

        snapshot = build_group_snapshot(self.group)
        self.assertEqual(snapshot["ranked"][0]["name"], self.schools[0].name)
        self.assertEqual(snapshot["ranked"][0]["completion"], 100)

    def test_a_school_outside_the_group_is_excluded(self):
        outside = School.objects.create(
            name="مدرسة خارجية", code="rep-outside", group=self.other_group
        )
        SchoolSubscription.objects.create(school=outside, plan=self.plan)

        snapshot = build_group_snapshot(self.group)
        self.assertNotIn(outside.name, [row["name"] for row in snapshot["rows"]])

    def test_the_workbook_is_produced(self):
        self._seed()
        payload = build_group_workbook_bytes(build_group_snapshot(self.group))

        self.assertTrue(payload)
        # توقيع ملف zip — وهو ما يكونه xlsx.
        self.assertEqual(payload[:2], b"PK")

    def test_the_preview_page_opens_for_the_director(self):
        self._seed()
        self._enter_director()

        response = self.client.get(reverse("reports:group_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مقارنة المدارس")

    def test_a_school_manager_cannot_open_the_group_report(self):
        self.client.force_login(self.managers[0])
        response = self.client.get(reverse("reports:group_report"))
        self.assertEqual(response.status_code, 404)

    def test_the_excel_download_returns_a_spreadsheet(self):
        self._seed()
        self._enter_director()

        response = self.client.get(reverse("reports:group_report_xlsx"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])

    def test_a_school_manager_cannot_download_the_group_export(self):
        self.client.force_login(self.managers[0])
        response = self.client.get(reverse("reports:group_report_xlsx"))
        self.assertEqual(response.status_code, 404)
