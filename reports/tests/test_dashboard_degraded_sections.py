# -*- coding: utf-8 -*-
"""قسمٌ تعثّر بناؤه لا يُعرض كقسمٍ لا بيانات له.

«لا يوجد نشاط» رسالةٌ صحيحة لمدرسةٍ جديدة، وكاذبةٌ تماماً لمدرسةٍ نشطة تعثّر
استعلامها. وكان الاثنان يُنتجان الشاشةَ نفسها — فيقرأ المدير العطلَ سكوناً،
ويصل بلاغُه بعد أيام بصيغة «القسم فارغ» لا «القسم معطّل».
"""
from __future__ import annotations

import logging
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class DashboardDegradedSectionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="degraded-dash")
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=10
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)

        self.manager = Teacher.objects.create_user(
            phone="500550055", name="مدير", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_empty_school_does_not_claim_a_failure(self):
        response = self.client.get(reverse("reports:admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["recent_activities_failed"])
        self.assertNotContains(response, "تعذّر تحميل آخر النشاطات")

    def test_failure_is_shown_as_a_failure_and_logged(self):
        with patch(
            "reports.views.schools._filter_by_school",
            side_effect=RuntimeError("تعثّر الاستعلام"),
        ):
            with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
                response = self.client.get(reverse("reports:admin_dashboard"))

        # الصفحة تُبنى — القرار الأصلي صحيح.
        self.assertEqual(response.status_code, 200)
        # والقسم يقول إنه تعثّر، لا أنه فارغ.
        self.assertTrue(response.context["recent_activities_failed"])
        self.assertContains(response, "تعذّر تحميل آخر النشاطات")
        # والأثر يصل السجل.
        self.assertTrue(
            any("dashboard.recent_activities" in line for line in captured.output),
            captured.output,
        )
