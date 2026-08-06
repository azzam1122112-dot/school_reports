# -*- coding: utf-8 -*-
"""نزاهة سجل الإجراءات وشفافيته.

الخاصية التي تحرسها هذه الاختبارات: **السجل شهادة لا سِجل عمل**. الشهادة تُكتب
مرة، ولا تُعدَّل، ولا تُمحى بحذف صاحبها، ولا يقرؤها إلا من يملك قراءتها. وكل
واحدة من هذه الأربع كانت مخروقة قبل هذه المرحلة.
"""
from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.audit_labels import describe
from reports.model_parts.audit import AuditLogImmutableError, audit_retention_purge
from reports.models import (
    AuditLog,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


class AuditLogImmutabilityTests(TestCase):
    """لا يُعدَّل ولا يُحذف — إلا عبر مسار الاحتفاظ المُسمّى."""

    def setUp(self):
        self.actor = _user("سارة", "0500000001")
        self.entry = AuditLog.objects.create(
            teacher=self.actor,
            actor_name="سارة",
            actor_role="معلمة",
            action=AuditLog.Action.CREATE,
            model_name="Report",
            object_repr="تقرير الأسبوع",
        )

    def test_saving_an_existing_row_is_rejected(self):
        self.entry.object_repr = "نص مزوَّر"
        with self.assertRaises(AuditLogImmutableError):
            self.entry.save()

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.object_repr, "تقرير الأسبوع")

    def test_bulk_update_is_rejected(self):
        """``queryset.update()`` هو الطريق الذي يلتف على ``save()`` — فيُغلق أيضاً."""
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.entry.pk).update(action=AuditLog.Action.LOGIN)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.action, AuditLog.Action.CREATE)

    def test_instance_delete_is_rejected(self):
        with self.assertRaises(AuditLogImmutableError):
            self.entry.delete()
        self.assertTrue(AuditLog.objects.filter(pk=self.entry.pk).exists())

    def test_bulk_delete_is_rejected(self):
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.entry.pk).delete()
        self.assertTrue(AuditLog.objects.filter(pk=self.entry.pk).exists())

    def test_retention_path_is_the_one_allowed_exit(self):
        """سياسة الاحتفاظ حاجة مشروعة، فتمر — لكن من باب واحد مُسمّى."""
        with audit_retention_purge():
            AuditLog.objects.filter(pk=self.entry.pk).delete()
        self.assertFalse(AuditLog.objects.filter(pk=self.entry.pk).exists())

    def test_retention_permission_does_not_leak_past_the_block(self):
        with audit_retention_purge():
            pass
        with self.assertRaises(AuditLogImmutableError):
            AuditLog.objects.filter(pk=self.entry.pk).delete()


class AuditActorSurvivesAccountDeletionTests(TestCase):
    """حذف الحساب يزيل صاحب الأثر لا الأثر — وإلا صار الحذف أداة طمس."""

    def test_actor_name_survives_and_row_is_kept(self):
        actor = _user("خالد", "0500000002")
        entry = AuditLog.objects.create(
            teacher=actor,
            actor_name="خالد",
            action=AuditLog.Action.DELETE,
            model_name="Report",
            object_repr="تقرير محذوف",
        )

        actor.delete()

        entry.refresh_from_db()
        self.assertIsNone(entry.teacher_id, "العلاقة تُفرَّغ ولا تُسقط الصف")
        self.assertEqual(entry.actor_name, "خالد")
        self.assertEqual(entry.actor_display, "خالد")

    def test_actor_name_is_captured_automatically_on_write(self):
        actor = _user("منى", "0500000003")
        entry = AuditLog.objects.create(
            teacher=actor,
            action=AuditLog.Action.CREATE,
            model_name="Ticket",
        )
        self.assertEqual(entry.actor_name, "منى")

    def test_display_falls_back_when_nothing_is_known(self):
        entry = AuditLog.objects.create(action=AuditLog.Action.LOGIN, model_name="Auth")
        self.assertEqual(entry.actor_display, "حساب محذوف")


class AuditEntryLabellingTests(TestCase):
    """الترجمة للعرض: تُعرِّب المعروف، ولا تسقط عند المجهول."""

    def test_known_model_and_action(self):
        view = describe(
            AuditLog(action="create", model_name="Report", object_repr="تقرير التهيئة")
        )
        self.assertEqual(view.headline, "إنشاء تقريراً")
        self.assertEqual(view.model_label, "تقرير")
        self.assertEqual(view.tone, "create")
        self.assertEqual(view.subject, "تقرير التهيئة")

    def test_session_events_carry_no_subject(self):
        view = describe(AuditLog(action="login", model_name="Auth"))
        self.assertEqual(view.headline, "تسجيل دخول")
        self.assertEqual(view.subject, "")
        self.assertEqual(view.tone, "session")

    def test_unknown_model_degrades_to_its_raw_name(self):
        """موديل جديد لم يُترجَم بعد يظهر باسمه — لا يختفي ولا يُسقط الصفحة."""
        view = describe(AuditLog(action="update", model_name="BrandNewThing"))
        self.assertIn("BrandNewThing", view.headline)
        self.assertEqual(view.model_label, "BrandNewThing")

    def test_unknown_action_degrades_safely(self):
        view = describe(AuditLog(action="teleport", model_name="Report"))
        self.assertEqual(view.tone, "update")
        self.assertIn("تقرير", view.headline)


@override_settings(ALLOWED_HOSTS=["testserver"])
class MyActivityLogPageTests(TestCase):
    """الصفحة تُظهر سجل صاحبها وحده — لا أكثر ولا أقل."""

    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="أساسية", price=0, days_duration=365, max_teachers=0
        )
        self.school = School.objects.create(name="مدرسة الأمل", code="amal")
        SchoolSubscription.objects.create(school=self.school, plan=plan)

        self.owner = _user("ليلى", "0500000010")
        self.other = _user("فهد", "0500000011")
        for user in (self.owner, self.other):
            SchoolMembership.objects.create(
                school=self.school,
                teacher=user,
                role_type=SchoolMembership.RoleType.TEACHER,
            )

        self.mine = AuditLog.objects.create(
            teacher=self.owner,
            school=self.school,
            actor_name="ليلى",
            action=AuditLog.Action.CREATE,
            model_name="Report",
            object_repr="تقرير خاص بليلى",
        )
        AuditLog.objects.create(
            teacher=self.other,
            school=self.school,
            actor_name="فهد",
            action=AuditLog.Action.CREATE,
            model_name="Report",
            object_repr="تقرير خاص بفهد",
        )
        self.url = reverse("reports:my_activity_log")

    def test_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("reports:login"), response["Location"])

    def test_page_shows_only_the_viewers_own_entries(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقرير خاص بليلى")
        self.assertNotContains(response, "تقرير خاص بفهد")

    def test_scope_cannot_be_widened_through_query_parameters(self):
        """لا يوجد معامل يوسّع النطاق — والمحاولة تُتجاهل لا تُطاع."""
        self.client.force_login(self.owner)
        response = self.client.get(self.url, {"teacher": self.other.pk})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "تقرير خاص بفهد")

    def test_action_filter_narrows_results(self):
        AuditLog.objects.create(
            teacher=self.owner,
            school=self.school,
            actor_name="ليلى",
            action=AuditLog.Action.DELETE,
            model_name="Ticket",
            object_repr="طلب ملغى",
        )
        self.client.force_login(self.owner)

        response = self.client.get(self.url, {"action": "delete"})
        self.assertContains(response, "طلب ملغى")
        self.assertNotContains(response, "تقرير خاص بليلى")

    def test_invalid_action_filter_is_ignored_rather_than_erroring(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url, {"action": "'; DROP TABLE--"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تقرير خاص بليلى")

    def test_filtered_empty_state_offers_a_way_back(self):
        """طريق مسدود بلا مخرج أسوأ عيوب التصفية — فالحالة الفارغة تحمل زر العودة."""
        self.client.force_login(self.owner)
        response = self.client.get(self.url, {"action": "delete"})

        self.assertContains(response, "لا توجد إجراءات مطابقة")
        self.assertContains(response, "عرض السجل كاملاً")

    def test_a_newcomer_still_sees_their_own_sign_in(self):
        """تسجيل الدخول نفسه إجراء مُسجَّل، فأول زيارة لا تكون شاشةً خاوية."""
        newcomer = _user("نورة", "0500000012")
        self.client.force_login(newcomer)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تسجيل دخول")
        self.assertNotContains(response, "تقرير خاص بليلى")


class AuditCoverageTests(TestCase):
    """ما يمسّ الصلاحيات والوثائق الرسمية مشمول بالتسجيل."""

    def test_permission_and_document_models_are_audited(self):
        """تغيير عضوية أو دور أخطر إجراء إداري في المنصة، وكان يمر بلا أثر.

        الاختبار يثبّت العضوية في القائمة لا في السلوك: السلوك يحتاج طلباً حياً
        (``get_current_request``)، لكن إسقاط الموديل من القائمة يُسكت التسجيل
        بلا أن يكسر شيئاً — وهو بالضبط النوع من الانحدار الذي يمر بلا ملاحظة.
        """
        from reports.model_parts.signals import (
            AUDITED_DELETE_MODELS,
            AUDITED_SAVE_MODELS,
        )

        audited_on_save = {model.__name__ for model in AUDITED_SAVE_MODELS}
        audited_on_delete = {model.__name__ for model in AUDITED_DELETE_MODELS}

        for name in ("SchoolMembership", "DepartmentMembership", "Notification"):
            self.assertIn(name, audited_on_save, f"{name} خارج تسجيل الحفظ")
            self.assertIn(name, audited_on_delete, f"{name} خارج تسجيل الحذف")

    def test_department_membership_is_attributed_to_its_school(self):
        """سجل بلا مدرسة لا يظهر في صفحة أي مدرسة — أي أنه سجل ضائع."""
        from reports.model_parts.signals import _audit_school_for
        from reports.models import Department, DepartmentMembership

        plan = SubscriptionPlan.objects.create(
            name="أساسية", price=0, days_duration=365, max_teachers=0
        )
        school = School.objects.create(name="مدرسة الرواد", code="ruwad")
        SchoolSubscription.objects.create(school=school, plan=plan)
        teacher = _user("عبدالله", "0500000030")
        department = Department.objects.create(school=school, name="النشاط", slug="activity")

        membership = DepartmentMembership(department=department, teacher=teacher)

        self.assertEqual(_audit_school_for(DepartmentMembership, membership), school)
