# reports/tests/test_tenant_isolation_matrix.py
# -*- coding: utf-8 -*-
"""حارس العزل المركزي — مدرستان، ومحاولة عبور من كل باب.

**لماذا اختبار مركزي وقد ثبتت صحة العزل في المراجعة؟** لأن المراجعة تُثبت
حال اليوم، والحارس يمنع انحدار الغد. والعزل هنا مفروض على ثلاث طبقات
مستقلة (Middleware، دوال الأدوار، فلتر مطويّ في ``restrict_queryset_for_user``)،
فكسر أيٍّ منها لا يُسقط الطلب بل **يوسّع النتيجة بصمت** — وهو نوع الخلل الذي
لا يُكتشف إلا من الخارج.

ولذلك يُختبر السلوك عبر HTTP لا الدوال مباشرةً: العرض القادم قد ينسى
استدعاء الدالة الصحيحة، والحارس يجب أن يمسك ذلك أيضاً.
"""
from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Report,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class TenantIsolationMatrixTests(TestCase):
    """مدير المدرسة (أ) لا يبلغ شيئاً من المدرسة (ب) بأي باب."""

    @classmethod
    def setUpTestData(cls):
        plan = SubscriptionPlan.objects.create(
            name="باقة اختبار العزل", price=0, days_duration=365, max_teachers=0
        )

        cls.school_a = School.objects.create(name="مدرسة أ", code="isolation-a")
        cls.school_b = School.objects.create(name="مدرسة ب", code="isolation-b")
        SchoolSubscription.objects.create(school=cls.school_a, plan=plan)
        SchoolSubscription.objects.create(school=cls.school_b, plan=plan)

        cls.manager_a = Teacher.objects.create_user(
            phone="500990001", name="مدير المدرسة أ", password="isolation-pass-a"
        )
        cls.manager_b = Teacher.objects.create_user(
            phone="500990002", name="مدير المدرسة ب", password="isolation-pass-b"
        )
        SchoolMembership.objects.create(
            school=cls.school_a,
            teacher=cls.manager_a,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        SchoolMembership.objects.create(
            school=cls.school_b,
            teacher=cls.manager_b,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        cls.report_b = Report.objects.create(
            school=cls.school_b,
            teacher=cls.manager_b,
            title="تقرير داخلي للمدرسة ب",
            report_date=timezone.localdate(),
        )
        cls.report_a = Report.objects.create(
            school=cls.school_a,
            teacher=cls.manager_a,
            title="تقرير داخلي للمدرسة أ",
            report_date=timezone.localdate(),
        )

    def _login_as_manager_a(self):
        self.client.force_login(self.manager_a)
        session = self.client.session
        session["active_school_id"] = self.school_a.pk
        session.save()

    # ── 1) الوصول المباشر بالمعرِّف (IDOR) ───────────────────────────
    def test_direct_id_access_to_another_schools_report_is_refused(self):
        """أخطر باب: تخمين رقم التقرير وفتحه مباشرةً."""
        self._login_as_manager_a()
        for url_name in ("report_print", "edit_my_report", "report_share_manage"):
            with self.subTest(url_name=url_name):
                response = self.client.get(
                    reverse(f"reports:{url_name}", args=[self.report_b.pk])
                )
                self.assertIn(
                    response.status_code,
                    (302, 403, 404),
                    f"{url_name} أعاد {response.status_code} لتقرير مدرسة أخرى",
                )
                if response.status_code == 200:  # pragma: no cover - حارس مضاعف
                    self.assertNotContains(response, self.report_b.title)

    # ── 2) تزوير المدرسة النشطة في الجلسة ────────────────────────────
    def test_forged_active_school_id_is_stripped_from_the_session(self):
        """قيمة الجلسة لا تُصدَّق: تُتحقَّق مقابل عضوية سارية في كل طلب."""
        self.client.force_login(self.manager_a)
        session = self.client.session
        session["active_school_id"] = self.school_b.pk  # لا عضوية له فيها
        session.save()

        response = self.client.get(reverse("reports:home"), HTTP_ACCEPT="text/html")
        self.assertNotEqual(
            self.client.session.get("active_school_id"),
            self.school_b.pk,
            "المدرسة المزوَّرة بقيت في الجلسة",
        )
        self.assertNotContains(response, "مدرسة ب", status_code=response.status_code)

    def test_forged_active_school_id_is_rejected_with_403_for_json_clients(self):
        self.client.force_login(self.manager_a)
        session = self.client.session
        session["active_school_id"] = self.school_b.pk
        session.save()

        response = self.client.get(
            reverse("reports:home"), HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.status_code, 403)

    # ── 3) كشف التقارير الإدارية ─────────────────────────────────────
    def test_admin_report_listing_never_includes_another_schools_report(self):
        self._login_as_manager_a()
        response = self.client.get(reverse("reports:admin_reports"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.report_a.title)
        self.assertNotContains(response, self.report_b.title)

    # ── 4) واجهة REST ────────────────────────────────────────────────
    def test_rest_api_reports_are_scoped_to_the_active_school(self):
        self._login_as_manager_a()
        response = self.client.get("/api/v1/reports/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = payload.get("results", payload if isinstance(payload, list) else [])
        returned_ids = {int(row["id"]) for row in rows}
        self.assertIn(self.report_a.pk, returned_ids)
        self.assertNotIn(self.report_b.pk, returned_ids)

    def test_rest_api_schools_lists_only_the_users_own_schools(self):
        self._login_as_manager_a()
        response = self.client.get("/api/v1/schools/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = payload.get("results", payload if isinstance(payload, list) else [])
        returned_ids = {int(row["id"]) for row in rows}
        self.assertEqual(returned_ids, {self.school_a.pk})

    # ── 5) عزل مفاتيح الكاش ──────────────────────────────────────────
    def test_every_cache_key_builder_carries_the_tenant_or_user_identity(self):
        """مفتاح بلا هوية مستأجر يعني استجابةَ مدرسةٍ تُقدَّم لأخرى."""
        from reports import cache_utils

        builders = (
            cache_utils.key_school_stats,
            cache_utils.key_department_list,
            cache_utils.key_reporttype_list,
            cache_utils.key_teacher_count,
            cache_utils.key_unread_count,
        )
        for builder in builders:
            with self.subTest(builder=builder.__name__):
                self.assertNotEqual(
                    builder(self.school_a.pk),
                    builder(self.school_b.pk),
                    f"{builder.__name__} ينتج المفتاح نفسه لمدرستين مختلفتين",
                )

        self.assertNotEqual(
            cache_utils._dashboard_payload_key(self.school_a.pk, "month", 1),
            cache_utils._dashboard_payload_key(self.school_b.pk, "month", 1),
        )

    # ── 6) العزل يفشل مغلقاً عند غياب السياق ─────────────────────────
    def test_missing_active_school_narrows_the_scope_instead_of_widening_it(self):
        """غياب المدرسة النشطة يجب ألا يعني «كل المدارس»."""
        from reports.services_reports import get_report_for_user_or_404
        from django.http import Http404

        with self.assertRaises(Http404):
            get_report_for_user_or_404(
                user=self.manager_a, pk=self.report_b.pk, active_school=None
            )
