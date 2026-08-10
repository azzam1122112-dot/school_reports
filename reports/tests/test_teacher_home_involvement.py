# -*- coding: utf-8 -*-
"""لوحة المنسوب تعرض ما هو طرفٌ فيه، وحال ما كتبه في دورة الاعتماد.

الشاشات الثلاث — التكليفات والاجتماعات والمبادرات — كانت موجودة ويصلها المنسوب
من القائمة الجانبية، لكن لوحته كانت تعرض تقاريره وطلباته وحدهما. فما كُلّف به
أو دُعي إليه أو اقترحه لا يُرى إلا إن عرف أين يبحث عنه، وهذا ما تثبّته
الاختبارات هنا: الصفحة الأولى تذكر الثلاثة، ولا تذكر ما يخصّ غيره.

وتثبّت معه ثلاثة قرارات يسهل انتقاضها بتعديلٍ لاحق:

- **المحضر على من يحرّره لا على من يحضره.** بندٌ لا يملك صاحبه إجراءً عليه
  يُدرّب قارئه على تجاهل البطاقة كلها.
- **ما أُعيد إلى صاحبه يُعرض ولو أُوقفت الدورة.** المفتاح يكتم وسم «معتمد»
  الروتيني، لا خبرَ عملٍ عالقٍ في ملعب صاحبه.
- **اللوحة تنادي صاحبها بما يُعرف به.** المدير له لوحته، ومن سواه — وكيلاً أو
  موظفاً إدارياً أو محضّر مختبر — يهبط هنا، فلا تُسمّيه الصفحةُ معلّماً.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reports.gender_labels import school_gender_labels
from reports.model_parts.approvals import ApprovalState
from reports.models import (
    Assignment,
    AssignmentTarget,
    Initiative,
    Meeting,
    MeetingAttendee,
    MeetingMinutes,
    Report,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
    TeacherAchievementFile,
)


class TeacherHomeInvolvementTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="باقة اللوحة", price=0, days_duration=365, max_teachers=0
        )
        self.school = School.objects.create(name="مدرسة اللوحة", code="home-school")
        SchoolSubscription.objects.create(school=self.school, plan=plan)

        self.manager = Teacher.objects.create_user(
            phone="0500040001", name="المدير", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        self.teacher = Teacher.objects.create_user(
            phone="0500040002", name="المعلّم", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.other = Teacher.objects.create_user(
            phone="0500040003", name="زميل", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        self.client.force_login(self.teacher)

    def _assignment(self, title, *, due_in_days=5):
        return Assignment.objects.create(
            scope=Assignment.Scope.SCHOOL,
            school=self.school,
            issuer=self.manager,
            title=title,
            due_at=timezone.now() + timedelta(days=due_in_days),
        )

    def _home(self):
        return self.client.get(reverse("reports:home"))

    # ------------------------------------------------------------------
    def test_an_assignment_targeting_the_teacher_reaches_the_dashboard(self):
        AssignmentTarget.objects.create(
            assignment=self._assignment("جرد المستودع"),
            assignee=self.teacher,
            school=self.school,
        )

        response = self._home()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ما أنا طرفٌ فيه")
        self.assertContains(response, "جرد المستودع")
        self.assertEqual(response.context["open_targets_count"], 1)

    def test_a_colleagues_assignment_stays_off_this_teachers_dashboard(self):
        AssignmentTarget.objects.create(
            assignment=self._assignment("مهمة الزميل"),
            assignee=self.other,
            school=self.school,
        )

        response = self._home()

        self.assertNotContains(response, "مهمة الزميل")
        self.assertEqual(response.context["open_targets_count"], 0)

    def test_an_approved_assignment_leaves_the_open_count(self):
        target = AssignmentTarget.objects.create(
            assignment=self._assignment("ما انتهى"),
            assignee=self.teacher,
            school=self.school,
        )
        AssignmentTarget.objects.filter(pk=target.pk).update(
            approval_state=ApprovalState.APPROVED
        )

        self.assertEqual(self._home().context["open_targets_count"], 0)

    def test_a_cancelled_assignment_leaves_the_open_count(self):
        assignment = self._assignment("تكليف ملغى")
        AssignmentTarget.objects.create(
            assignment=assignment,
            assignee=self.teacher,
            school=self.school,
        )
        assignment.cancel(by=self.manager, reason="تغيّرت الأولويات")

        response = self._home()

        self.assertEqual(response.context["open_targets_count"], 0)
        self.assertNotContains(response, "تكليف ملغى")

    def test_an_overdue_assignment_is_counted_as_needing_attention(self):
        AssignmentTarget.objects.create(
            assignment=self._assignment("متأخر", due_in_days=-3),
            assignee=self.teacher,
            school=self.school,
        )

        response = self._home()

        self.assertEqual(response.context["overdue_targets_count"], 1)
        self.assertContains(response, "تكليفات تجاوزت موعدها")

    def test_a_meeting_the_teacher_is_invited_to_reaches_the_dashboard(self):
        meeting = Meeting.objects.create(
            scope=Meeting.Scope.SCHOOL,
            school=self.school,
            organizer=self.manager,
            title="مجلس المعلمين",
            scheduled_at=timezone.now() + timedelta(days=2),
            status=Meeting.Status.SCHEDULED,
        )
        MeetingAttendee.objects.create(meeting=meeting, person=self.teacher)

        response = self._home()

        self.assertContains(response, "مجلس المعلمين")
        self.assertEqual(response.context["upcoming_meetings_count"], 1)

    def test_a_meeting_without_the_teacher_stays_off_the_dashboard(self):
        meeting = Meeting.objects.create(
            scope=Meeting.Scope.SCHOOL,
            school=self.school,
            organizer=self.manager,
            title="لجنة لا تخصّه",
            scheduled_at=timezone.now() + timedelta(days=2),
            status=Meeting.Status.SCHEDULED,
        )
        MeetingAttendee.objects.create(meeting=meeting, person=self.other)

        response = self._home()

        self.assertNotContains(response, "لجنة لا تخصّه")
        self.assertEqual(response.context["upcoming_meetings_count"], 0)

    def _held_meeting(self, title, *, organizer):
        return Meeting.objects.create(
            scope=Meeting.Scope.SCHOOL,
            school=self.school,
            organizer=organizer,
            title=title,
            scheduled_at=timezone.now() - timedelta(days=1),
            status=Meeting.Status.HELD,
        )

    def test_a_held_meeting_this_teacher_organised_awaits_their_minutes(self):
        self._held_meeting("اجتماع نظّمته", organizer=self.teacher)

        response = self._home()

        self.assertEqual(response.context["pending_minutes_count"], 1)
        self.assertContains(response, "محاضر بانتظار تحريرها")

    def test_a_held_meeting_this_teacher_records_awaits_their_minutes(self):
        meeting = self._held_meeting("اجتماع أحرّر محضره", organizer=self.manager)
        MeetingAttendee.objects.create(meeting=meeting, person=self.teacher)
        MeetingMinutes.objects.create(meeting=meeting, recorder=self.teacher)

        self.assertEqual(self._home().context["pending_minutes_count"], 1)

    def test_merely_attending_a_held_meeting_puts_no_minutes_on_the_teacher(self):
        """المحضر عمل من يحرّره: كتابته على الموظف الإداري واعتماده على المدير.

        وضعُه على كل حاضرٍ يعطيه بنداً لا يملك عليه إجراءً.
        """
        meeting = self._held_meeting("اجتماع حضرته فقط", organizer=self.manager)
        MeetingAttendee.objects.create(meeting=meeting, person=self.teacher)

        response = self._home()

        self.assertEqual(response.context["pending_minutes_count"], 0)
        self.assertNotContains(response, "محاضر بانتظار تحريرها")

    def test_an_approved_minute_leaves_the_pending_count(self):
        meeting = self._held_meeting("اجتماع اكتمل محضره", organizer=self.teacher)
        MeetingMinutes.objects.create(
            meeting=meeting,
            recorder=self.teacher,
            approval_state=ApprovalState.APPROVED,
        )

        self.assertEqual(self._home().context["pending_minutes_count"], 0)

    def test_the_teachers_own_initiative_reaches_the_dashboard(self):
        Initiative.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="مبادرة القراءة",
            summary="فكرة وأثر",
        )

        response = self._home()

        self.assertContains(response, "مبادرة القراءة")
        self.assertEqual(response.context["draft_initiatives_count"], 1)

    def test_a_colleagues_initiative_stays_off_this_teachers_dashboard(self):
        Initiative.objects.create(
            school=self.school,
            teacher=self.other,
            title="مبادرة الزميل",
            summary="فكرة وأثر",
        )

        self.assertNotContains(self._home(), "مبادرة الزميل")

    def test_the_card_disappears_when_the_teacher_is_party_to_nothing(self):
        response = self._home()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ما أنا طرفٌ فيه")

    def test_a_returned_achievement_file_reaches_follow_today_with_its_note(self):
        TeacherAchievementFile.objects.create(
            teacher=self.teacher,
            school=self.school,
            academic_year="1447-1448",
            status=TeacherAchievementFile.Status.RETURNED,
            manager_notes="أكمل شواهد التطوير المهني",
        )

        response = self._home()

        self.assertEqual(response.context["returned_achievement_count"], 1)
        self.assertContains(response, "ملف الإنجاز معاد لاستكماله")
        self.assertContains(response, "أكمل شواهد التطوير المهني")
        self.assertNotContains(response, "كل شيء محدث")

    def test_a_colleagues_returned_achievement_file_stays_off_the_dashboard(self):
        TeacherAchievementFile.objects.create(
            teacher=self.other,
            school=self.school,
            academic_year="1447-1448",
            status=TeacherAchievementFile.Status.RETURNED,
            manager_notes="ملاحظة لا تخص المعلم",
        )

        response = self._home()

        self.assertEqual(response.context["returned_achievement_count"], 0)
        self.assertNotContains(response, "ملاحظة لا تخص المعلم")


class HomeReportApprovalTests(TestCase):
    """اللوحة تقول أين وقف كل تقرير من دورته."""

    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="باقة الاعتماد", price=0, days_duration=365, max_teachers=0
        )
        self.school = School.objects.create(
            name="مدرسة الاعتماد", code="home-approval", report_approval_enabled=True
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)

        self.teacher = Teacher.objects.create_user(
            phone="0500050001", name="المعلّم", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.client.force_login(self.teacher)

    def _report(self, title, state, *, note=""):
        report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title=title,
            report_date=date(2026, 8, 1),
        )
        Report.objects.filter(pk=report.pk).update(
            approval_state=state, review_note=note
        )
        return report

    def _home(self):
        return self.client.get(reverse("reports:home"))

    # ------------------------------------------------------------------
    def test_a_returned_report_reaches_the_dashboard_with_its_note(self):
        self._report(
            "تقرير الإذاعة",
            ApprovalState.RETURNED,
            note="أرفق صور التنفيذ",
        )

        response = self._home()

        self.assertEqual(response.context["returned_reports_count"], 1)
        self.assertContains(response, "تقارير أُعيدت إليك")
        self.assertContains(response, "أرفق صور التنفيذ")

    def test_a_report_awaiting_information_counts_with_the_returned(self):
        self._report("تقرير ناقص", ApprovalState.NEEDS_INFO)

        self.assertEqual(self._home().context["returned_reports_count"], 1)

    def test_a_report_awaiting_review_is_not_put_on_its_owner(self):
        self._report("تقرير مُرسل", ApprovalState.SUBMITTED)

        response = self._home()

        self.assertEqual(response.context["returned_reports_count"], 0)
        self.assertEqual(response.context["awaiting_review_count"], 1)
        self.assertNotContains(response, "تقارير أُعيدت إليك")

    def test_an_unsent_draft_is_named_so_its_owner_knows_it_never_left(self):
        self._report("مسودة", ApprovalState.DRAFT)

        response = self._home()

        self.assertEqual(response.context["draft_reports_count"], 1)
        self.assertContains(response, "مسودة لم تُرسل بعد")

    def test_an_approved_report_carries_its_state_beside_it(self):
        self._report("تقرير معتمد", ApprovalState.APPROVED)

        response = self._home()

        self.assertTrue(response.context["report_approval_enabled"])
        # `class="..."` لا `.th-state`: الثاني في ورقة الأنماط على كل حال.
        self.assertContains(response, 'class="th-state"')

    def test_a_colleagues_returned_report_stays_off_this_dashboard(self):
        other = Teacher.objects.create_user(
            phone="0500050002", name="زميل", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        report = Report.objects.create(
            school=self.school,
            teacher=other,
            title="تقرير الزميل",
            report_date=date(2026, 8, 1),
        )
        Report.objects.filter(pk=report.pk).update(
            approval_state=ApprovalState.RETURNED
        )

        response = self._home()

        self.assertEqual(response.context["returned_reports_count"], 0)
        self.assertNotContains(response, "تقرير الزميل")

    def test_a_school_without_the_cycle_carries_no_state_badges(self):
        School.objects.filter(pk=self.school.pk).update(report_approval_enabled=False)
        self._report("تقرير عادي", ApprovalState.APPROVED)

        response = self._home()

        self.assertFalse(response.context["report_approval_enabled"])
        self.assertNotContains(response, 'class="th-state"')

    def test_stopping_the_cycle_does_not_hide_a_report_already_returned(self):
        """المفتاح يكتم الوسم الروتيني لا خبر عملٍ عالقٍ في ملعب صاحبه."""
        School.objects.filter(pk=self.school.pk).update(report_approval_enabled=False)
        self._report("تقرير عالق", ApprovalState.RETURNED)

        response = self._home()

        self.assertEqual(response.context["returned_reports_count"], 1)
        self.assertContains(response, "تقارير أُعيدت إليك")


class HomeRoleFramingTests(TestCase):
    """اللوحة تنادي صاحبها بما يُعرف به."""

    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="باقة المسمّيات", price=0, days_duration=365, max_teachers=0
        )
        self.school = School.objects.create(name="مدرسة المسمّيات", code="home-roles")
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.labels = school_gender_labels(self.school)

    def _login_as(self, phone, name, *, role_type, job_title):
        user = Teacher.objects.create_user(
            phone=phone, name=name, password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=user,
            role_type=role_type,
            job_title=job_title,
        )
        self.client.force_login(user)
        return user

    def test_the_lab_technician_is_not_called_a_teacher_by_their_own_page(self):
        self._login_as(
            "0500060001",
            "المحضّر",
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
            job_title=SchoolMembership.JobTitle.LAB_TECH,
        )

        response = self.client.get(reverse("reports:home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["USER_ROLE_LABEL"], str(self.labels["lab_tech"])
        )
        self.assertContains(response, f"مساحة عمل {self.labels['lab_tech']}")

    def test_the_teacher_keeps_the_wording_they_had(self):
        self._login_as(
            "0500060002",
            "المعلّم",
            role_type=SchoolMembership.RoleType.TEACHER,
            job_title=SchoolMembership.JobTitle.TEACHER,
        )

        response = self.client.get(reverse("reports:home"))

        self.assertContains(response, f"مساحة عمل {self.labels['teacher']}")
