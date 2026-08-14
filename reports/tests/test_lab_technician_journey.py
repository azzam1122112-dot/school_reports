# -*- coding: utf-8 -*-
"""رحلة محضّر المختبر: عهدةٌ وتجاربُ فوق كل ما للمعلّم.

كان المحضّر مسمّى عرضياً لا أكثر: ترويسةٌ تناديه «محضر مختبر» وشاشاتٌ لا تعرف
مختبراً. فصار له عهدةٌ تُجرَد وتُسلَّم، وتجاربُ تُوثَّق وتمرّ بالاعتماد — **مع
بقاء كل ما للمعلّم**: تقاريره وملف إنجازه وطلباته وتكليفاته.

والحدود هي أهمّ ما يُحرَس هنا:

- **من يسجّل ليس من يتابع.** حاملُ ``manage_lab`` يقرأ ويراجع ولا يكتب — وإلا
  راجع جردَ نفسه.
- **العهدة بلا اعتماد والتجربة به.** الجرد يوصف واقعاً، والتجربة عملٌ يُعتمد.
- **الحساب من الحركة لا من حقل مخزَّن**، فلا يقول الجرد إن خارج المختبر خمساً
  من أربع.
"""
from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from reports import capabilities as caps
from reports.model_parts.approvals import ApprovalState
from reports.models import (
    Department,
    LabAsset,
    LabAssetHandover,
    LabExperiment,
    School,
    SchoolMembership,
    SchoolSubscription,
    StaffScope,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import can_record_lab, can_view_lab, is_lab_technician
from reports.services_lab import lab_summary, outstanding_handovers, record_handover

PASSWORD = "Passw0rd!123"


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password=PASSWORD)


def _school(name: str, code: str) -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=0
    )
    school = School.objects.create(name=name, code=code)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


class LabTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.school = _school("ثانوية المختبر", "lab-school")
        self.department = Department.objects.create(
            school=self.school, name="قسم العلوم", slug="lab-science"
        )

        self.manager = _user("مدير المختبر", "0500031001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        self.tech = _user("محضر المختبر", "0500031002")
        self.tech_membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.tech,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
            job_title=SchoolMembership.JobTitle.LAB_TECH,
        )

        self.teacher = _user("معلم الأحياء", "0500031003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        self.deputy = _user("وكيل المختبر", "0500031004")
        self.deputy_membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )

    # ------------------------------------------------------------------
    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _page(self, user, url_name, *args) -> str:
        self._enter(user)
        return self.client.get(reverse(url_name, args=args)).content.decode()

    def _grant_lab_watch(self):
        scope, _ = StaffScope.objects.get_or_create(membership=self.deputy_membership)
        scope.capabilities = [caps.MANAGE_LAB]
        scope.save()
        scope.departments.set([self.department])
        cache.clear()
        return scope

    def _asset(self, name="ميكروسكوب", quantity=4, **kwargs):
        return LabAsset.objects.create(
            school=self.school,
            name=name,
            quantity=quantity,
            recorded_by=self.tech,
            custodian=self.tech,
            **kwargs,
        )

    def _experiment(self, **kwargs):
        defaults = {
            "school": self.school,
            "recorder": self.tech,
            "title": "استخلاص الكلوروفيل",
            "experiment_date": date(2026, 8, 1),
            "procedure": "طحن الورق ثم الترشيح.",
        }
        defaults.update(kwargs)
        return LabExperiment.objects.create(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# مَن هو المحضّر، ومَن يسجّل، ومَن يتابع
# ═══════════════════════════════════════════════════════════════════════
@override_settings(ALLOWED_HOSTS=["testserver"])
class WhoOwnsTheLabTests(LabTestCase):
    def test_the_technician_is_detected_by_their_job_title(self):
        self.assertTrue(is_lab_technician(self.tech, self.school))

    def test_a_plain_admin_employee_is_not_a_technician(self):
        staff = _user("موظف شؤون", "0500031010")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=staff,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
            job_title=SchoolMembership.JobTitle.ADMIN_STAFF,
        )
        self.assertFalse(is_lab_technician(staff, self.school))

    def test_a_technician_in_one_school_is_not_one_in_another(self):
        other = _school("مدرسة أخرى", "lab-other")
        self.assertFalse(is_lab_technician(self.tech, other))

    def test_a_role_question_without_a_school_answers_no(self):
        self.assertFalse(is_lab_technician(self.tech))

    def test_the_legacy_teacher_row_still_counts_as_a_technician(self):
        """من أُضيف قبل توحيد بابَي الإسناد يحمل المسمّى مع دور معلّم."""
        legacy = _user("محضر قديم", "0500031011")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=legacy,
            role_type=SchoolMembership.RoleType.TEACHER,
            job_title=SchoolMembership.JobTitle.LAB_TECH,
        )
        self.assertTrue(is_lab_technician(legacy, self.school))

    def test_the_technician_and_the_manager_may_record(self):
        self.assertTrue(can_record_lab(self.tech, self.school))
        self.assertTrue(can_record_lab(self.manager, self.school))

    def test_a_teacher_may_neither_record_nor_view(self):
        self.assertFalse(can_record_lab(self.teacher, self.school))
        self.assertFalse(can_view_lab(self.teacher, self.school))

    def test_the_watcher_views_but_never_records(self):
        """الفرق الذي يحفظ المراجعة: من يشرف لا يكتب ما يشرف عليه."""
        self._grant_lab_watch()
        self.assertTrue(can_view_lab(self.deputy, self.school))
        self.assertFalse(can_record_lab(self.deputy, self.school))

    def test_the_watcher_dashboard_offers_browsing_not_recording_actions(self):
        self._grant_lab_watch()
        self._enter(self.deputy)

        response = self.client.get(reverse("reports:lab_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "استعراض عهدة المختبر")
        self.assertContains(response, "استعراض سجل التجارب")
        self.assertNotContains(response, "إضافة صنف للعهدة")
        self.assertNotContains(response, "توثيق تجربة جديدة")

    def test_a_bare_deputy_role_opens_nothing(self):
        self.assertFalse(can_view_lab(self.deputy, self.school))


# ═══════════════════════════════════════════════════════════════════════
# العهدة
# ═══════════════════════════════════════════════════════════════════════
@override_settings(ALLOWED_HOSTS=["testserver"])
class LabCustodyTests(LabTestCase):
    def test_the_technician_opens_and_fills_the_inventory(self):
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_assets"),
            {
                "name": "ميكروسكوب ضوئي",
                "code": "M-14",
                "category": LabAsset.Category.DEVICE,
                "quantity": 3,
                "unit": "قطعة",
                "condition": LabAsset.Condition.GOOD,
                "location": "دولاب ٢",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        asset = LabAsset.objects.get(school=self.school, name="ميكروسكوب ضوئي")
        self.assertEqual(asset.quantity, 3)
        # المحضّر صاحب العهدة افتراضاً، فلا يُطلب منه تسمية نفسه في كل صنف.
        self.assertEqual(asset.custodian_id, self.tech.pk)

    def test_a_new_inventory_item_cannot_start_with_zero_quantity(self):
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_assets"),
            {
                "name": "صنف بلا كمية",
                "category": LabAsset.Category.TOOL,
                "quantity": 0,
                "condition": LabAsset.Condition.GOOD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LabAsset.objects.filter(name="صنف بلا كمية").exists())
        self.assertContains(response, "أدخل قطعة واحدة على الأقل")

    def test_a_teacher_cannot_reach_the_inventory(self):
        self._enter(self.teacher)
        response = self.client.get(reverse("reports:lab_assets"))
        self.assertRedirects(response, reverse("reports:home"))

    def test_the_watcher_reads_the_inventory_but_cannot_add(self):
        self._grant_lab_watch()
        self._asset()
        self._enter(self.deputy)
        self.assertEqual(self.client.get(reverse("reports:lab_assets")).status_code, 200)

        response = self.client.post(
            reverse("reports:lab_assets"),
            {"name": "دخيل", "category": LabAsset.Category.TOOL, "quantity": 1,
             "condition": LabAsset.Condition.GOOD},
            follow=True,
        )
        self.assertFalse(LabAsset.objects.filter(name="دخيل").exists())
        self.assertContains(response, "لا تُجيز التسجيل")

    def test_an_asset_from_another_school_is_not_found(self):
        other = _school("مدرسة ثالثة", "lab-third")
        stranger = LabAsset.objects.create(school=other, name="غريب", quantity=1)
        self._enter(self.tech)
        response = self.client.get(
            reverse("reports:lab_asset_detail", args=[stranger.pk])
        )
        self.assertEqual(response.status_code, 404)

    # ── الحركة ────────────────────────────────────────────────────────
    def test_handing_out_reduces_what_is_available(self):
        asset = self._asset(quantity=4)
        record_handover(
            asset,
            direction=LabAssetHandover.Direction.OUT,
            person=self.teacher,
            quantity=1,
            actor=self.tech,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.out_quantity, 1)
        self.assertEqual(asset.available_quantity, 3)

    def test_returning_restores_what_is_available(self):
        asset = self._asset(quantity=4)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=2, actor=self.tech,
        )
        record_handover(
            asset, direction=LabAssetHandover.Direction.IN,
            person=self.teacher, quantity=2, actor=self.tech,
        )
        self.assertEqual(asset.available_quantity, 4)

    def test_more_cannot_leave_than_the_lab_holds(self):
        asset = self._asset(quantity=2)
        with self.assertRaises(ValidationError):
            record_handover(
                asset, direction=LabAssetHandover.Direction.OUT,
                person=self.teacher, quantity=3, actor=self.tech,
            )

    def test_more_cannot_return_than_actually_left(self):
        asset = self._asset(quantity=4)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        with self.assertRaises(ValidationError):
            record_handover(
                asset, direction=LabAssetHandover.Direction.IN,
                person=self.teacher, quantity=2, actor=self.tech,
            )

    def test_the_recipient_name_is_snapshotted(self):
        """الكشف يبقى مقروءاً بعد حذف الحساب."""
        asset = self._asset()
        handover = record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        self.assertEqual(handover.person_name, self.teacher.name)
        self.teacher.delete()
        handover.refresh_from_db()
        self.assertEqual(handover.person_name, "معلم الأحياء")

    def test_outstanding_lists_what_is_still_out_and_with_whom(self):
        asset = self._asset(quantity=5)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=3, actor=self.tech,
        )
        record_handover(
            asset, direction=LabAssetHandover.Direction.IN,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        rows = outstanding_handovers(self.school)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["person_name"], self.teacher.name)

    def test_a_fully_returned_asset_leaves_the_outstanding_list(self):
        asset = self._asset(quantity=2)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        record_handover(
            asset, direction=LabAssetHandover.Direction.IN,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        self.assertEqual(outstanding_handovers(self.school), [])

    def test_handing_out_through_the_screen(self):
        asset = self._asset(quantity=3)
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_asset_action", args=[asset.pk]),
            {
                "lab_action": "handover",
                "direction": LabAssetHandover.Direction.OUT,
                "person": self.teacher.pk,
                "quantity": 1,
            },
            follow=True,
        )
        self.assertContains(response, "سُجِّل التسليم")
        self.assertEqual(asset.out_quantity, 1)

    def test_handing_out_requires_naming_the_recipient(self):
        asset = self._asset()
        self._enter(self.tech)
        self.client.post(
            reverse("reports:lab_asset_action", args=[asset.pk]),
            {"lab_action": "handover", "direction": LabAssetHandover.Direction.OUT, "quantity": 1},
            follow=True,
        )
        self.assertEqual(asset.handovers.count(), 0)

    # ── الحالة والإخراج ───────────────────────────────────────────────
    def test_an_asset_partly_handed_out_is_not_marked_missing(self):
        """ما في يد معلّمٍ معروف ليس مفقوداً — هو مُسلَّم."""
        asset = self._asset(quantity=2)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_asset_action", args=[asset.pk]),
            {"lab_action": "condition", "condition": LabAsset.Condition.MISSING},
            follow=True,
        )
        asset.refresh_from_db()
        self.assertNotEqual(asset.condition, LabAsset.Condition.MISSING)
        self.assertContains(response, "مُسلَّم")

    def test_retiring_keeps_the_asset_and_its_movement_log(self):
        asset = self._asset(quantity=2)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=1, actor=self.tech,
        )
        self._enter(self.tech)
        self.client.post(
            reverse("reports:lab_asset_action", args=[asset.pk]),
            {"lab_action": "retire"},
            follow=True,
        )
        asset.refresh_from_db()
        self.assertFalse(asset.is_active)
        self.assertEqual(asset.handovers.count(), 1)

    def test_the_quantity_cannot_drop_below_what_is_handed_out(self):
        asset = self._asset(quantity=4)
        record_handover(
            asset, direction=LabAssetHandover.Direction.OUT,
            person=self.teacher, quantity=3, actor=self.tech,
        )
        self._enter(self.tech)
        self.client.post(
            reverse("reports:lab_asset_detail", args=[asset.pk]),
            {
                "name": asset.name,
                "category": asset.category,
                "quantity": 1,
                "condition": asset.condition,
            },
            follow=True,
        )
        asset.refresh_from_db()
        self.assertEqual(asset.quantity, 4)

    # ── الطباعة ───────────────────────────────────────────────────────
    def test_the_inventory_prints_with_a_signature_line(self):
        self._asset(name="أنبوب اختبار", quantity=20)
        page = self._page(self.tech, "reports:lab_assets_print")
        self.assertIn("كشف عهدة المختبر", page)
        self.assertIn("أنبوب اختبار", page)
        self.assertIn("مدير المدرسة", page)
        self.assertIn("العودة إلى العهدة", page)
        self.assertIn("طباعة الكشف", page)
        self.assertNotIn('onload="window.print()"', page)

    def test_the_print_page_carries_no_dark_mode_layer(self):
        """الورق أبيض دائماً — وهو عُرف كل صفحات الطباعة هنا."""
        page = self._page(self.tech, "reports:lab_assets_print")
        self.assertNotIn("theme-manager.js", page)
        self.assertNotIn("dark-mode.css", page)


# ═══════════════════════════════════════════════════════════════════════
# التجارب ودورة اعتمادها
# ═══════════════════════════════════════════════════════════════════════
@override_settings(ALLOWED_HOSTS=["testserver"])
class LabExperimentTests(LabTestCase):
    def test_the_technician_documents_an_experiment_as_a_draft(self):
        self._enter(self.tech)
        self.client.post(
            reverse("reports:lab_experiments"),
            {
                "title": "تفاعل الحديد بالكبريت",
                "experiment_date": "2026-08-02",
                "subject": "كيمياء",
                "class_name": "أول ثانوي",
                "students_count": 24,
                "procedure": "خلط المسحوقين ثم التسخين.",
            },
            follow=True,
        )
        experiment = LabExperiment.objects.get(title="تفاعل الحديد بالكبريت")
        self.assertEqual(experiment.recorder_id, self.tech.pk)
        self.assertEqual(experiment.approval_state, ApprovalState.DRAFT)

    def test_an_incomplete_experiment_can_be_saved_as_a_draft(self):
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_experiments"),
            {"title": "", "experiment_date": "", "procedure": ""},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        experiment = LabExperiment.objects.get(recorder=self.tech)
        self.assertEqual(experiment.title, "")
        self.assertIsNone(experiment.experiment_date)
        self.assertEqual(experiment.approval_state, ApprovalState.DRAFT)
        self.assertContains(response, "مسودة تجربة بلا عنوان")
        self.assertContains(response, "أكمل هذه البيانات قبل الإرسال")

    def test_experiment_screen_explains_draft_and_submission_requirements(self):
        page = self._page(self.tech, "reports:lab_experiments")
        self.assertIn("يمكن حفظ المسودة الآن وإكمالها لاحقاً", page)
        self.assertIn("يلزم قبل الإرسال", page)
        self.assertIn("احفظ المسودة", page)

    def test_an_experiment_without_steps_cannot_be_submitted(self):
        experiment = self._experiment(procedure="")
        with self.assertRaises(ValidationError):
            experiment.assert_ready_for_submission()

    def test_the_technician_submits_and_the_manager_approves(self):
        experiment = self._experiment()

        self._enter(self.tech)
        self.client.post(
            reverse("reports:lab_experiment_action", args=[experiment.pk]),
            {"approval_action": "submit"},
            follow=True,
        )
        experiment.refresh_from_db()
        self.assertEqual(experiment.approval_state, ApprovalState.SUBMITTED)

        self._enter(self.manager)
        self.client.post(
            reverse("reports:lab_experiment_action", args=[experiment.pk]),
            {"approval_action": "approve"},
            follow=True,
        )
        experiment.refresh_from_db()
        self.assertEqual(experiment.approval_state, ApprovalState.APPROVED)

    def test_nobody_approves_their_own_experiment(self):
        """قاعدةٌ لا تُستثنى في المشروع كله."""
        experiment = self._experiment(recorder=self.manager)
        self._enter(self.manager)
        self.client.post(
            reverse("reports:lab_experiment_action", args=[experiment.pk]),
            {"approval_action": "submit"},
            follow=True,
        )
        experiment.refresh_from_db()
        self.client.post(
            reverse("reports:lab_experiment_action", args=[experiment.pk]),
            {"approval_action": "approve"},
            follow=True,
        )
        experiment.refresh_from_db()
        self.assertNotEqual(experiment.approval_state, ApprovalState.APPROVED)

    def test_an_approved_experiment_is_no_longer_editable(self):
        experiment = self._experiment(approval_state=ApprovalState.APPROVED)
        self._enter(self.tech)
        response = self.client.post(
            reverse("reports:lab_experiment_detail", args=[experiment.pk]),
            {
                "title": "عنوان جديد",
                "experiment_date": "2026-08-03",
                "procedure": "خطوات أخرى",
            },
            follow=True,
        )
        experiment.refresh_from_db()
        self.assertEqual(experiment.title, "استخلاص الكلوروفيل")
        self.assertContains(response, "معتمدة")

    def test_the_watcher_may_review_but_not_record(self):
        self._grant_lab_watch()
        experiment = self._experiment(approval_state=ApprovalState.SUBMITTED)
        self._enter(self.deputy)
        response = self.client.get(
            reverse("reports:lab_experiment_detail", args=[experiment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_record"])
        self.assertFalse(response.context["editable"])

    def test_a_teacher_cannot_open_an_experiment(self):
        experiment = self._experiment()
        self._enter(self.teacher)
        response = self.client.get(
            reverse("reports:lab_experiment_detail", args=[experiment.pk])
        )
        self.assertRedirects(response, reverse("reports:home"))

    def test_only_offered_actions_are_accepted(self):
        """لا زرّ لا تذكره ``available_actions`` — ولا فعلٌ يُنفَّذ بدونها."""
        experiment = self._experiment()
        self._enter(self.teacher)
        self.client.post(
            reverse("reports:lab_experiment_action", args=[experiment.pk]),
            {"approval_action": "approve"},
        )
        experiment.refresh_from_db()
        self.assertEqual(experiment.approval_state, ApprovalState.DRAFT)

    def test_an_experiment_prints_with_its_steps(self):
        experiment = self._experiment(safety_notes="قفازات ونظارات واقية.")
        page = self._page(self.tech, "reports:lab_experiment_print", experiment.pk)
        self.assertIn("استخلاص الكلوروفيل", page)
        self.assertIn("طحن الورق", page)
        self.assertIn("قفازات", page)
        self.assertIn("العودة إلى المحضر", page)
        self.assertIn("طباعة المحضر", page)
        self.assertNotIn('onload="window.print()"', page)


# ═══════════════════════════════════════════════════════════════════════
# الرحلة كاملة: المختبر فوق ما للمعلّم لا بدلاً منه
# ═══════════════════════════════════════════════════════════════════════
@override_settings(ALLOWED_HOSTS=["testserver"])
class TheTechnicianKeepsEveryTeacherFeatureTests(LabTestCase):
    def test_the_technician_still_reaches_every_teacher_screen(self):
        """المحضّر معلّمٌ في كل ما سوى المختبر — وهذا نصّ توصيفه."""
        self._enter(self.tech)
        for name in (
            "reports:home",
            "reports:my_reports",
            "reports:add_report",
            "reports:achievement_my_files",
            "reports:my_requests",
            "reports:my_assignments",
            "reports:meeting_list",
            "reports:my_notifications",
            "reports:my_work_archive",
            "reports:my_activity_log",
        ):
            with self.subTest(destination=name):
                self.assertEqual(
                    self.client.get(reverse(name)).status_code, 200, name
                )

    def test_the_lab_card_joins_the_technicians_home(self):
        self._asset()
        page = self._page(self.tech, "reports:home")
        self.assertIn("المختبر", page)
        self.assertIn(reverse("reports:lab_assets"), page)

    def test_the_home_still_shows_the_technicians_own_reports_card(self):
        """البطاقة تُضاف ولا تُبدِل: لوحته لوحة المعلّم وفوقها المختبر."""
        page = self._page(self.tech, "reports:home")
        self.assertIn(reverse("reports:my_reports"), page)
        self.assertIn(reverse("reports:achievement_my_files"), page)

    def test_the_lab_card_stays_off_a_teachers_home(self):
        self._asset()
        page = self._page(self.teacher, "reports:home")
        self.assertNotIn(reverse("reports:lab_assets"), page)

    def test_the_bar_offers_the_technician_their_lab(self):
        page = self._page(self.tech, "reports:home")
        for name in (
            "reports:lab_dashboard",
            "reports:lab_assets",
            "reports:lab_experiments",
        ):
            with self.subTest(destination=name):
                self.assertIn(f'href="{reverse(name)}"', page)

    def test_the_bar_hides_the_lab_from_a_teacher(self):
        page = self._page(self.teacher, "reports:home")
        self.assertNotIn(f'href="{reverse("reports:lab_dashboard")}"', page)

    def test_the_watcher_is_offered_the_lab_too(self):
        self._grant_lab_watch()
        page = self._page(self.deputy, "reports:home")
        self.assertIn(f'href="{reverse("reports:lab_dashboard")}"', page)

    def test_the_header_still_calls_them_a_lab_technician(self):
        page = self._page(self.tech, "reports:home")
        self.assertIn("محضر مختبر", page)

    def test_the_dashboard_summarises_the_lab(self):
        self._asset(condition=LabAsset.Condition.DAMAGED)
        self._experiment()
        self._enter(self.tech)
        response = self.client.get(reverse("reports:lab_dashboard"))
        self.assertEqual(response.status_code, 200)
        summary = response.context["summary"]
        self.assertEqual(summary["assets_total"], 1)
        self.assertEqual(summary["assets_damaged"], 1)
        self.assertEqual(summary["experiments_total"], 1)

    def test_the_summary_counts_are_school_scoped(self):
        other = _school("مدرسة رابعة", "lab-fourth")
        LabAsset.objects.create(school=other, name="خارج النطاق", quantity=9)
        self._asset()
        summary = lab_summary(self.school)
        self.assertEqual(summary["assets_total"], 1)
