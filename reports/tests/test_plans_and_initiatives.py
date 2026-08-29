# -*- coding: utf-8 -*-
"""الخطط والمبادرات.

خاصيتان مركزيتان:

1. **نسبة إنجاز الخطة محسوبة من تنفيذ مهامها** — لا مُدخَلة يدوياً. رقمٌ يكتبه
   صاحب الخطة عن خطته يقيس تفاؤله لا تنفيذها.
2. **الممارسة لا تُشارَك قبل اعتمادها** — ومشاركةُ غير المعتمد نقلٌ إلى مدارس
   أخرى لما لم تتحقق منه مدرستها بعد.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports import capabilities as caps
from reports.model_parts.approvals import ApprovalState
from reports.models import (
    Assignment,
    Department,
    Initiative,
    Plan,
    PlanGoal,
    PlanTask,
    School,
    SchoolGroup,
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
from reports.services_plans import (
    PlanError,
    convert_task_to_assignment,
    initiatives_visible_to,
    share_initiative,
    shared_practices_for_group,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


class PlanBase(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="باقة", price=0, days_duration=365, max_teachers=0
        )
        self.group = SchoolGroup.objects.create(name="مجمع", code="plan-group")
        self.school = School.objects.create(
            name="مدرسة الخطط", code="plan-school", group=self.group
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)

        self.manager = _user("المدير", "0500070001")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.teacher = _user("المعلم", "0500070002")
        SchoolMembership.objects.create(
            school=self.school, teacher=self.teacher, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.department = Department.objects.create(
            school=self.school, name="التعليمية", slug="edu-plan"
        )

    def _plan(self, owner=None, **overrides):
        data = {
            "scope": Plan.Scope.SCHOOL,
            "school": self.school,
            "owner": owner or self.manager,
            "title": "خطة تحسين نواتج التعلم",
            "academic_year": "1447-1448",
        }
        data.update(overrides)
        return Plan.objects.create(**data)

    def _task(self, plan, **overrides):
        data = {
            "plan": plan,
            "title": "تنفيذ ورشة تدريبية",
            "order": (plan.tasks.count() + 1),
        }
        data.update(overrides)
        return PlanTask.objects.create(**data)


class PlanModelTests(PlanBase):
    def test_a_school_plan_requires_a_school(self):
        plan = Plan(scope=Plan.Scope.SCHOOL, owner=self.manager, title="بلا مدرسة")
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_a_group_plan_requires_a_group(self):
        plan = Plan(scope=Plan.Scope.GROUP, owner=self.manager, title="بلا مجموعة")
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_dates_must_be_ordered(self):
        plan = Plan(
            scope=Plan.Scope.SCHOOL,
            school=self.school,
            owner=self.manager,
            title="خطة",
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 8, 1),
        )
        with self.assertRaises(ValidationError):
            plan.full_clean()

    def test_progress_is_zero_without_tasks(self):
        self.assertEqual(self._plan().progress_percent, 0)

    def test_progress_counts_only_completed_tasks(self):
        """محسوبة من التنفيذ لا من تقدير يُدخَل يدوياً."""
        plan = self._plan()
        first = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        self._task(plan, title="مهمة ثانية", responsible=self.teacher,
                   due_at=timezone.now() + timedelta(days=4))

        self.assertEqual(plan.progress_percent, 0)

        convert_task_to_assignment(first, self.manager)
        target = first.assignment.targets.get()
        submit(target, self.teacher, school=self.school)
        approve(target, self.manager, school=self.school)

        plan.refresh_from_db()
        self.assertEqual(plan.progress_percent, 50)

    def test_an_untracked_task_is_never_done(self):
        plan = self._plan()
        task = self._task(plan)
        self.assertFalse(task.is_done)
        self.assertEqual(task.state, "untracked")

    def test_a_plan_without_tasks_cannot_be_submitted(self):
        plan = self._plan()
        with self.assertRaises(ValidationError):
            submit(plan, self.manager, school=self.school)


class TaskToAssignmentTests(PlanBase):
    """الجسر إلى التنفيذ."""

    def test_converting_requires_a_responsible(self):
        plan = self._plan()
        task = self._task(plan, due_at=timezone.now() + timedelta(days=4))
        with self.assertRaises(PlanError):
            convert_task_to_assignment(task, self.manager)

    def test_converting_requires_a_due_date(self):
        plan = self._plan()
        task = self._task(plan, responsible=self.teacher)
        with self.assertRaises(PlanError):
            convert_task_to_assignment(task, self.manager)

    def test_the_assignment_carries_the_plan_as_its_source(self):
        plan = self._plan()
        task = self._task(
            plan,
            responsible=self.teacher,
            department=self.department,
            due_at=timezone.now() + timedelta(days=6),
        )
        assignment = convert_task_to_assignment(task, self.manager)

        self.assertEqual(assignment.source, Assignment.Source.PLAN)
        self.assertEqual(assignment.school_id, self.school.pk)
        self.assertEqual(assignment.department_id, self.department.pk)
        self.assertEqual(assignment.targets.get().assignee_id, self.teacher.pk)

        task.refresh_from_db()
        self.assertEqual(task.assignment_id, assignment.pk)

    def test_the_first_conversion_moves_the_plan_into_execution(self):
        """مرحلةٌ تُدار يدوياً تبقى على «قيد الإعداد» بعد أشهر من العمل."""
        plan = self._plan()
        self.assertEqual(plan.stage, Plan.Stage.PREPARING)

        task = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        convert_task_to_assignment(task, self.manager)

        plan.refresh_from_db()
        self.assertEqual(plan.stage, Plan.Stage.RUNNING)

    def test_an_approved_plan_still_offers_task_conversion(self):
        """اعتماد الخطة يجمّد بنودها، لكنه لا يمنع بدء تنفيذ مهامها."""
        plan = self._plan()
        task = self._task(
            plan,
            responsible=self.teacher,
            due_at=timezone.now() + timedelta(days=4),
        )
        issue(plan, self.manager, school=self.school)
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

        detail = self.client.get(reverse("reports:plan_detail", args=[plan.pk]))

        self.assertContains(detail, "حوّلها إلى تكليف")
        self.assertContains(detail, 'name="plan_action" value="track_task"')

        response = self.client.post(
            reverse("reports:plan_action", args=[plan.pk]),
            {"plan_action": "track_task", "task_id": task.pk},
        )
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertIsNotNone(task.assignment_id)

    def test_converting_twice_is_refused(self):
        plan = self._plan()
        task = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        convert_task_to_assignment(task, self.manager)

        with self.assertRaises(PlanError):
            convert_task_to_assignment(task, self.manager)

    def test_an_unrelated_user_cannot_convert(self):
        plan = self._plan()
        task = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        with self.assertRaises(PermissionDenied):
            convert_task_to_assignment(task, self.teacher)

    def test_a_late_task_reports_lateness(self):
        plan = self._plan()
        task = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        convert_task_to_assignment(task, self.manager)
        Assignment.objects.filter(pk=task.assignment_id).update(
            due_at=timezone.now() - timedelta(days=1)
        )
        task.refresh_from_db()
        self.assertEqual(task.state, "late")


class PlanApprovalTests(PlanBase):
    def test_the_manager_issues_their_own_school_plan(self):
        """صاحب الوثيقة وصاحب سلطتها معاً — فيُصدر ولا ينتظر مراجعاً."""
        plan = self._plan()
        self._task(plan)

        actions = available_actions(plan, self.manager, school=self.school)
        self.assertIn("issue", actions)
        self.assertNotIn("submit", actions)

        issue(plan, self.manager, school=self.school)
        self.assertEqual(plan.approval_state, ApprovalState.APPROVED)

    def test_a_deputy_plan_follows_the_review_route(self):
        """خطةٌ يُعدّها وكيل تمر بالمراجعة كما يمر أي عمل يُرفع لمن فوقه."""
        deputy = _user("الوكيل", "0500070010")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        scope = StaffScope.objects.create(
            membership=membership, capabilities=[caps.TRACK_PLANS]
        )
        scope.departments.add(self.department)

        plan = self._plan(owner=deputy, title="خطة الوكيل")
        self._task(plan)

        actions = available_actions(plan, deputy, school=self.school)
        self.assertIn("submit", actions)
        self.assertNotIn("issue", actions)

        submit(plan, deputy, school=self.school)
        approve(plan, self.manager, school=self.school)
        self.assertEqual(plan.approval_state, ApprovalState.APPROVED)

    def test_a_deputy_cannot_approve_their_own_plan(self):
        """الرفض هو الخاصية — لا نوع الاستثناء.

        النظام يوقفه عند بوابة السلطة قبل أن يبلغ فحص الاعتماد الذاتي، وكلا
        الردّين رفضٌ صحيح. وتثبيتُ نوع بعينه هنا يجعل الاختبار يكسر عند أي
        إعادة ترتيب للبوابات وإن بقيت الخاصية سليمة.
        """
        deputy = _user("وكيل", "0500070011")
        SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        plan = self._plan(owner=deputy)
        self._task(plan)
        submit(plan, deputy, school=self.school)

        with self.assertRaises((PermissionDenied, ApprovalError)):
            approve(plan, deputy, school=self.school)

        plan.refresh_from_db()
        self.assertNotEqual(plan.approval_state, ApprovalState.APPROVED)


class InitiativeTests(PlanBase):
    def _initiative(self, teacher=None, **overrides):
        data = {
            "school": self.school,
            "teacher": teacher or self.teacher,
            "title": "ركن القراءة",
            "summary": "تخصيص ركن للقراءة الحرة وأثره على الإقبال.",
        }
        data.update(overrides)
        return Initiative.objects.create(**data)

    def test_a_teacher_proposes_and_the_manager_approves(self):
        initiative = self._initiative()
        self.assertEqual(initiative.approval_state, ApprovalState.DRAFT)

        submit(initiative, self.teacher, school=self.school)
        approve(initiative, self.manager, school=self.school)
        self.assertEqual(initiative.approval_state, ApprovalState.APPROVED)

    def test_the_manager_issues_their_own_initiative(self):
        """المدير صاحب المبادرة وسلطتها، فيُصدرها ولا يرفعها لنفسه."""
        initiative = self._initiative(teacher=self.manager, title="مبادرة المدير")

        actions = available_actions(initiative, self.manager, school=self.school)
        self.assertIn("issue", actions)
        self.assertNotIn("submit", actions)

        issue(initiative, self.manager, school=self.school)
        self.assertEqual(initiative.approval_state, ApprovalState.APPROVED)
        self.assertEqual(initiative.decided_by_id, self.manager.pk)

    def test_approved_school_initiatives_are_visible_to_staff(self):
        initiative = self._initiative(teacher=self.manager, title="مبادرة مدرسية")
        issue(initiative, self.manager, school=self.school)

        visible = initiatives_visible_to(self.teacher, self.school)
        self.assertTrue(visible.filter(pk=initiative.pk).exists())

    def test_another_staff_members_draft_remains_private(self):
        other = _user("معلم آخر", "0500070012")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        initiative = self._initiative(teacher=other, title="مسودة خاصة")

        self.assertFalse(
            initiatives_visible_to(self.teacher, self.school).filter(pk=initiative.pk).exists()
        )
        self.assertTrue(
            initiatives_visible_to(self.manager, self.school).filter(pk=initiative.pk).exists()
        )

    def test_an_empty_initiative_cannot_be_submitted(self):
        initiative = self._initiative(summary="   ")
        with self.assertRaises(ValidationError):
            submit(initiative, self.teacher, school=self.school)

    def test_a_teacher_cannot_approve_their_own_initiative(self):
        initiative = self._initiative()
        submit(initiative, self.teacher, school=self.school)

        with self.assertRaises((PermissionDenied, ApprovalError)):
            approve(initiative, self.teacher, school=self.school)

        initiative.refresh_from_db()
        self.assertNotEqual(initiative.approval_state, ApprovalState.APPROVED)

    def test_sharing_before_approval_is_refused(self):
        """نقلٌ إلى مدارس أخرى لما لم تتحقق منه مدرستها بعد."""
        initiative = self._initiative()
        with self.assertRaises(PlanError):
            share_initiative(initiative, self.manager)

    def test_only_the_manager_shares(self):
        initiative = self._initiative()
        submit(initiative, self.teacher, school=self.school)
        approve(initiative, self.manager, school=self.school)

        with self.assertRaises(PermissionDenied):
            share_initiative(initiative, self.teacher)

    def test_sharing_marks_it_as_a_best_practice(self):
        initiative = self._initiative()
        submit(initiative, self.teacher, school=self.school)
        approve(initiative, self.manager, school=self.school)
        share_initiative(initiative, self.manager)

        initiative.refresh_from_db()
        self.assertTrue(initiative.is_shared)
        self.assertTrue(initiative.is_best_practice)

    def test_the_group_sees_only_shared_and_approved_practices(self):
        approved_only = self._initiative(title="معتمدة غير مشاركة")
        submit(approved_only, self.teacher, school=self.school)
        approve(approved_only, self.manager, school=self.school)

        shared = self._initiative(title="مشاركة")
        submit(shared, self.teacher, school=self.school)
        approve(shared, self.manager, school=self.school)
        share_initiative(shared, self.manager)

        draft = self._initiative(title="مسودة")

        titles = [item.title for item in shared_practices_for_group(self.group)]
        self.assertEqual(titles, ["مشاركة"])
        self.assertNotIn(approved_only.title, titles)
        self.assertNotIn(draft.title, titles)


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlanScreenTests(PlanBase):
    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_the_manager_opens_the_plan_list(self):
        self._plan()
        self._enter(self.manager)

        response = self.client.get(reverse("reports:plan_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "خطة تحسين نواتج التعلم")

    def test_a_teacher_cannot_create_a_plan(self):
        self._enter(self.teacher)
        response = self.client.get(reverse("reports:plan_create"))
        self.assertEqual(response.status_code, 302)

    def test_creating_a_plan_from_the_screen(self):
        self._enter(self.manager)
        response = self.client.post(
            reverse("reports:plan_create"),
            {
                "title": "خطة الفصل الثاني",
                "description": "رفع مستوى الأنشطة",
                "academic_year": "1447-1448",
                "starts_on": "",
                "ends_on": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Plan.objects.filter(title="خطة الفصل الثاني").exists())

    def test_a_task_owner_may_open_the_plan(self):
        """من يُنفّذ جزءاً من خطة يحق له أن يرى موقعه منها."""
        plan = self._plan()
        self._task(plan, responsible=self.teacher)
        self._enter(self.teacher)

        response = self.client.get(reverse("reports:plan_detail", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)

    def test_an_unrelated_teacher_reads_it_as_missing(self):
        plan = self._plan()
        stranger = _user("غريب", "0500070020")
        SchoolMembership.objects.create(
            school=self.school, teacher=stranger, role_type=SchoolMembership.RoleType.TEACHER
        )
        self._enter(stranger)

        response = self.client.get(reverse("reports:plan_detail", args=[plan.pk]))
        self.assertEqual(response.status_code, 404)

    def test_goals_and_tasks_are_added_and_numbered(self):
        plan = self._plan()
        self._enter(self.manager)

        self.client.post(
            reverse("reports:plan_action", args=[plan.pk]),
            {"plan_action": "add_goal", "title": "رفع نسبة الإتقان", "indicator": "نسبة", "target": "80%"},
        )
        self.client.post(
            reverse("reports:plan_action", args=[plan.pk]),
            {"plan_action": "add_task", "title": "ورشة", "description": "", "goal": "", "responsible": "", "department": "", "due_at": ""},
        )

        self.assertEqual(PlanGoal.objects.filter(plan=plan).count(), 1)
        self.assertEqual(PlanTask.objects.filter(plan=plan).count(), 1)

    def test_a_tracked_task_cannot_be_deleted(self):
        plan = self._plan()
        task = self._task(plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4))
        convert_task_to_assignment(task, self.manager)
        self._enter(self.manager)

        self.client.post(
            reverse("reports:plan_action", args=[plan.pk]),
            {"plan_action": "remove_task", "task_id": task.pk},
        )
        self.assertTrue(PlanTask.objects.filter(pk=task.pk).exists())

    def test_the_assignee_sees_the_plan_task_as_work(self):
        plan = self._plan()
        task = self._task(
            plan, title="تجهيز معرض", responsible=self.teacher,
            due_at=timezone.now() + timedelta(days=4),
        )
        convert_task_to_assignment(task, self.manager)
        self._enter(self.teacher)

        response = self.client.get(reverse("reports:my_assignments"))
        self.assertContains(response, "تجهيز معرض")

    def test_the_owner_edits_the_plan_document(self):
        plan = self._plan()
        self._enter(self.manager)

        page = self.client.get(reverse("reports:plan_edit", args=[plan.pk]))
        self.assertEqual(page.status_code, 200)

        response = self.client.post(
            reverse("reports:plan_edit", args=[plan.pk]),
            {
                "title": "خطة تحسين نواتج التعلم (منقّحة)",
                "description": "نسخة معدّلة",
                "academic_year": "1447-1448",
                "starts_on": "",
                "ends_on": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.title, "خطة تحسين نواتج التعلم (منقّحة)")
        # التعديل لا يمس ملكية الخطة ولا نطاقها.
        self.assertEqual(plan.owner_id, self.manager.pk)
        self.assertEqual(plan.school_id, self.school.pk)

    def test_an_unrelated_teacher_cannot_reach_the_edit_screen(self):
        plan = self._plan()
        stranger = _user("غريب على الخطة", "0500070041")
        SchoolMembership.objects.create(
            school=self.school, teacher=stranger, role_type=SchoolMembership.RoleType.TEACHER
        )
        self._enter(stranger)

        response = self.client.get(reverse("reports:plan_edit", args=[plan.pk]))
        self.assertEqual(response.status_code, 404)

    def test_an_approved_plan_is_not_edited(self):
        plan = self._plan()
        self._task(plan)
        issue(plan, self.manager, school=self.school)
        self._enter(self.manager)

        response = self.client.get(reverse("reports:plan_edit", args=[plan.pk]))
        self.assertEqual(response.status_code, 302)

    def test_a_draft_plan_is_deleted(self):
        plan = self._plan()
        self._enter(self.manager)

        response = self.client.post(reverse("reports:plan_delete", args=[plan.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Plan.objects.filter(pk=plan.pk).exists())

    def test_a_plan_whose_tasks_became_assignments_is_not_deleted(self):
        """تكليفٌ قائم بلا خطة يُعرف منها سببه — فتُغلق الخطة ولا تُحذف."""
        plan = self._plan()
        task = self._task(
            plan, responsible=self.teacher, due_at=timezone.now() + timedelta(days=4)
        )
        convert_task_to_assignment(task, self.manager)
        self._enter(self.manager)

        self.client.post(reverse("reports:plan_delete", args=[plan.pk]))
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_an_approved_plan_is_not_deleted(self):
        plan = self._plan()
        self._task(plan)
        issue(plan, self.manager, school=self.school)
        self._enter(self.manager)

        self.client.post(reverse("reports:plan_delete", args=[plan.pk]))
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_a_teacher_cannot_delete_a_plan_they_only_execute(self):
        plan = self._plan()
        self._task(plan, responsible=self.teacher)
        self._enter(self.teacher)

        self.client.post(reverse("reports:plan_delete", args=[plan.pk]))
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_the_plan_prints_with_its_goals_and_tasks(self):
        plan = self._plan()
        goal = PlanGoal.objects.create(plan=plan, order=1, title="رفع نسبة الإتقان")
        self._task(plan, title="ورشة القياس", goal=goal, responsible=self.teacher)
        self._enter(self.manager)

        response = self.client.get(reverse("reports:plan_print", args=[plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "رفع نسبة الإتقان")
        self.assertContains(response, "ورشة القياس")
        self.assertContains(response, self.teacher.name)

    def test_the_screens_offer_printing_and_sharing(self):
        plan = self._plan()
        self._enter(self.manager)

        listing = self.client.get(reverse("reports:plan_list"))
        self.assertContains(listing, reverse("reports:plan_print", args=[plan.pk]))

        detail = self.client.get(reverse("reports:plan_detail", args=[plan.pk]))
        self.assertContains(detail, reverse("reports:plan_print", args=[plan.pk]))
        self.assertContains(detail, reverse("reports:plan_edit", args=[plan.pk]))
        self.assertContains(
            detail, f"http://testserver{reverse('reports:plan_detail', args=[plan.pk])}"
        )

    def test_a_teacher_proposes_an_initiative_from_the_screen(self):
        self._enter(self.teacher)
        response = self.client.post(
            reverse("reports:initiative_list"),
            {"title": "نادي العلوم", "summary": "نادٍ أسبوعي للتجارب.", "plan": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Initiative.objects.filter(title="نادي العلوم").exists())

    def test_a_manager_created_initiative_is_issued_and_visible_to_the_teacher(self):
        self._enter(self.manager)
        response = self.client.post(
            reverse("reports:initiative_list"),
            {
                "title": "مبادرة الإتقان",
                "summary": "برنامج مدرسي لرفع جودة التعلم.",
                "plan": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        initiative = Initiative.objects.get(title="مبادرة الإتقان")
        self.assertEqual(initiative.approval_state, ApprovalState.APPROVED)
        self.assertEqual(initiative.decided_by_id, self.manager.pk)

        self._enter(self.teacher)
        teacher_page = self.client.get(reverse("reports:initiative_list"))
        self.assertContains(teacher_page, initiative.title)
        self.assertContains(teacher_page, "معتمد")

    def test_a_manager_cannot_issue_an_empty_initiative(self):
        self._enter(self.manager)
        response = self.client.post(
            reverse("reports:initiative_list"),
            {"title": "مبادرة ناقصة", "summary": "", "plan": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Initiative.objects.filter(title="مبادرة ناقصة").exists())
        self.assertContains(response, "اشرح فكرة المبادرة وأثرها")

    def test_a_teacher_does_not_see_other_peoples_initiatives(self):
        other = _user("زميل", "0500070030")
        SchoolMembership.objects.create(
            school=self.school, teacher=other, role_type=SchoolMembership.RoleType.TEACHER
        )
        Initiative.objects.create(
            school=self.school, teacher=other, title="مبادرة الزميل", summary="نص"
        )
        self._enter(self.teacher)

        response = self.client.get(reverse("reports:initiative_list"))
        self.assertNotContains(response, "مبادرة الزميل")

    def test_the_manager_sees_submitted_initiatives(self):
        initiative = Initiative.objects.create(
            school=self.school, teacher=self.teacher, title="مبادرة معلّم", summary="نص"
        )
        submit(initiative, self.teacher, school=self.school)
        self._enter(self.manager)

        response = self.client.get(reverse("reports:initiative_list"))
        self.assertContains(response, "مبادرة معلّم")
