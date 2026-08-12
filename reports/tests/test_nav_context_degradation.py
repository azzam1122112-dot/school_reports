# -*- coding: utf-8 -*-
"""يحرس أن شريط التنقل لا يتعثّر صامتاً.

هذه الوحدة تعمل في **كل طلب**. وحين كانت تبتلع استثناءاتها بلا أثر، عاش فيها
عطلٌ كامل بلا أن يظهر: ``_targeted_for_user_q`` كانت تبني
``Q(recipients=user)`` على علاقةٍ عكسية لا تقبل كائن مستخدم، فترمي ``ValueError``
عند تنفيذ الاستعلام في كل نداء — فمات مسار الاحتياط للإشعار البارز وللعدّاد
جميعاً، ولم يُسجَّل ذلك مرةً واحدة.

فالاختباران الأولان يقفلان العطل نفسه، والباقي يقفل السلوك الذي يمنع عودة
الصمت.
"""
from __future__ import annotations

import logging

from django.test import RequestFactory, TestCase, override_settings

from reports.context_processors import (
    _targeted_for_user_q,
    _user_lookup_for_relation,
    nav_context,
)
from reports.models import (
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class TargetedForUserQueryTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="nav-degrade-school")
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.user = Teacher.objects.create_user(
            phone="500770077", name="معلم", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def test_recipients_relation_resolves_through_the_join_model(self):
        """``recipients`` علاقةٌ عكسية إلى ``NotificationRecipient`` لا إلى المستخدم."""
        lookup = _user_lookup_for_relation(Notification, "recipients")
        self.assertEqual(lookup, "recipients__teacher")

    def test_targeting_query_executes_instead_of_raising(self):
        """العطل الأصلي: الاستعلام كان يرمي ``ValueError`` عند التنفيذ."""
        notification = Notification.objects.create(
            title="إشعار", message="نص", school=self.school
        )
        NotificationRecipient.objects.create(
            notification=notification, teacher=self.user
        )

        matched = list(
            Notification.objects.filter(_targeted_for_user_q(Notification, self.user))
            .distinct()
        )
        self.assertIn(notification, matched)

    def test_targeting_excludes_notifications_addressed_to_others(self):
        other = Teacher.objects.create_user(
            phone="500770078", name="آخر", password="pass"
        )
        theirs = Notification.objects.create(
            title="لغيره", message="نص", school=self.school
        )
        NotificationRecipient.objects.create(notification=theirs, teacher=other)

        matched = list(
            Notification.objects.filter(_targeted_for_user_q(Notification, self.user))
            .distinct()
        )
        self.assertNotIn(theirs, matched)


@override_settings(ALLOWED_HOSTS=["testserver"], NAV_CONTEXT_CACHE_TTL_SECONDS=0)
class NavContextDegradationTests(TestCase):
    """الشريط يُكمل عند التعثّر — لكن التعثّر يُترك للعِيان."""

    def setUp(self):
        self.factory = RequestFactory()
        self.school = School.objects.create(name="مدرسة", code="nav-degrade-2")
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.user = Teacher.objects.create_user(
            phone="500770079", name="معلم", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def _request(self):
        request = self.factory.get("/")
        request.user = self.user
        request.session = {"active_school_id": self.school.id}
        request.COOKIES = {}
        return request

    def test_healthy_request_logs_no_degradation(self):
        """المسار السليم صامت — وإلا صار السجلّ ضجيجاً لا يُقرأ."""
        logger = logging.getLogger("tawtheeq.degraded")
        with self.assertNoLogs(logger, level=logging.ERROR):
            context = nav_context(self._request())

        self.assertIn("NAV_NOTIFICATIONS_UNREAD", context)

    def test_counter_failure_is_logged_and_does_not_break_the_bar(self):
        from unittest.mock import patch

        with patch(
            "reports.context_processors._unread_count",
            side_effect=RuntimeError("تعذّر العدّ"),
        ):
            with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
                context = nav_context(self._request())

        # الصفحة تُبنى — وهو القرار الأصلي الصحيح.
        self.assertEqual(context["NAV_NOTIFICATIONS_UNREAD"], 0)
        # والتعثّر يصل السجل — وهو ما كان مفقوداً.
        self.assertTrue(
            any("nav.unread_count_outer" in line for line in captured.output),
            captured.output,
        )
