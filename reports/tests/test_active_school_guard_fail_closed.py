# -*- coding: utf-8 -*-
"""``ActiveSchoolGuardMiddleware`` لا يُرفق مدرسةً لم تثبت عضويتها.

العقد مكتوب في ``config/settings.py`` فوق ``SchoolRateLimitMiddleware``:
«ActiveSchoolGuard قد خوّل وأرفق ``request.active_school``». وعليه بُني إسقاطُ
المحدِّد لاستعلامه، وعليه تعتمد عروضٌ تقرأ ``request.active_school`` بوصفه
مُخوَّلاً لا مجرّد ما في الجلسة.

وكانت الدالة تنتهي بـ ``request.active_school = school_obj`` في **كل** مسار:
من لا عضوية له، ومن تعثّر فحص عضويته. فيُمسح مفتاحه من الجلسة — أي أن الطلب
**التالي** سليم — لكن الطلب الجاري يمضي ومعه مدرسةٌ ليست له.

هذه الاختبارات تقفل المسارات الثلاثة.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from reports.middleware import ActiveSchoolGuardMiddleware
from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ActiveSchoolGuardFailClosedTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )

        self.mine = School.objects.create(name="مدرستي", code="guard-mine")
        self.theirs = School.objects.create(name="مدرسة غيري", code="guard-theirs")
        SchoolSubscription.objects.create(school=self.mine, plan=plan)
        SchoolSubscription.objects.create(school=self.theirs, plan=plan)

        self.user = Teacher.objects.create_user(
            phone="500660066", name="معلم", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.mine,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def _run(self, school_id, *, user=None, json_request=False):
        headers = {"HTTP_ACCEPT": "application/json"} if json_request else {}
        request = self.factory.get("/dashboard/", **headers)
        request.user = user if user is not None else self.user
        request.session = {"active_school_id": school_id}

        middleware = ActiveSchoolGuardMiddleware(lambda req: HttpResponse("ok"))
        response = middleware(request)
        return request, response

    def test_member_gets_the_school_attached(self):
        request, response = self._run(self.mine.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.active_school, self.mine)
        self.assertIsNotNone(getattr(request, "_active_membership", None))

    def test_non_member_never_gets_a_school_attached(self):
        """العطل الأصلي: كان يُرفق ``theirs`` رغم انعدام العضوية."""
        request, _response = self._run(self.theirs.id)

        self.assertIsNone(request.active_school)
        # والجلسة تُنظَّف كذلك، فلا يتكرّر في الطلب التالي.
        self.assertNotIn("active_school_id", request.session)

    def test_non_member_json_request_is_forbidden(self):
        _request, response = self._run(self.theirs.id, json_request=True)

        self.assertEqual(response.status_code, 403)

    def test_membership_lookup_failure_denies_instead_of_attaching(self):
        """تعثّر الفحص ليس إذناً: يُصفَّر ويُسجَّل."""
        with patch(
            "reports.models.SchoolMembership.objects.filter",
            side_effect=RuntimeError("قاعدة البيانات تعثّرت"),
        ):
            with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
                request, _response = self._run(self.mine.id)

        self.assertIsNone(request.active_school)
        self.assertTrue(
            any("tenant.membership_check" in line for line in captured.output),
            captured.output,
        )

    def test_inactive_school_is_not_attached(self):
        self.mine.is_active = False
        self.mine.save(update_fields=["is_active"])

        request, _response = self._run(self.mine.id)

        self.assertIsNone(request.active_school)

    def test_anonymous_user_gets_no_school(self):
        request, response = self._run(self.mine.id, user=AnonymousUser())

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(request.active_school)
