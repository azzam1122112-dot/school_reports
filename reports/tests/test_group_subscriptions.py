# -*- coding: utf-8 -*-
"""كشف اشتراكات مدارس المجموعة — شاشة المدير التنفيذي.

الثقب الذي أُغلق: المدير التنفيذي كان يرى «حالة الاشتراك» عموداً داخل جدول
المدارس، فلا يعرف أي مدرسة تنتهي هذا الأسبوع إلا بقراءة الجدول صفّاً صفّاً، ولا
يعرف بمن يتصل إذا عرف. وهذه الشاشة تجمع ذلك مرتَّباً بالأولوية ومعه رقم مدير كل
مدرسة.

وما يُحرَس هنا ثلاثة: أن الترتيب يقدّم ما يحتاج إجراءً، وأن العزل قائم (لا تُرى
مدرسةٌ خارج مجموعته ولا يبلغ الصفحةَ غيرُ المدير التنفيذي)، وأن الشاشة بقيت
قراءةً محضة بلا زرّ شراء.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


def _plan(name: str, days: int = 365, seats: int = 25) -> SubscriptionPlan:
    return SubscriptionPlan.objects.create(
        name=name, price=1000, days_duration=days, max_teachers=seats
    )


def _subscribe(school, plan, *, ends_in_days: int) -> SchoolSubscription:
    """اشتراك ينتهي بعد عدد أيام محدَّد.

    ``SchoolSubscription.save`` يُعيد حساب التواريخ عند الإنشاء، فتمريرها في
    ``create`` لا يثبت. لذلك تُضبط بعد الإنشاء حيث لا يُعاد الحساب.
    """
    today = timezone.localdate()
    subscription = SchoolSubscription.objects.create(school=school, plan=plan)
    subscription.start_date = today - timedelta(days=10)
    subscription.end_date = today + timedelta(days=ends_in_days)
    subscription.save(update_fields=["start_date", "end_date"])
    return subscription


@override_settings(ALLOWED_HOSTS=["testserver"])
class GroupSubscriptionsScreenTests(TestCase):
    def setUp(self):
        self.group = SchoolGroup.objects.create(name="مجموعة النور", code="noor")
        self.other_group = SchoolGroup.objects.create(name="مجموعة أخرى", code="other")

        plan = _plan("باقة سنوية")

        self.healthy = School.objects.create(
            name="مدرسة مستقرة", code="calm", group=self.group
        )
        _subscribe(self.healthy, plan, ends_in_days=300)

        self.urgent = School.objects.create(
            name="مدرسة عاجلة", code="rush", group=self.group
        )
        _subscribe(self.urgent, plan, ends_in_days=3)

        # مدرسة بلا اشتراك إطلاقاً: حالة مشروعة يجب أن تُعرض لا أن تُسقط الصفحة.
        self.bare = School.objects.create(
            name="مدرسة بلا اشتراك", code="bare", group=self.group
        )

        self.outsider = School.objects.create(
            name="مدرسة خارج المجموعة", code="out", group=self.other_group
        )
        _subscribe(self.outsider, plan, ends_in_days=200)

        self.manager = Teacher.objects.create_user(
            phone="500990011", name="مديرة العاجلة", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.urgent,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        self.director = Teacher.objects.create_user(
            phone="500990022", name="المدير التنفيذي", password="pass"
        )
        SchoolGroupMembership.objects.create(
            group=self.group,
            user=self.director,
            role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR,
        )

        self.url = reverse("reports:group_subscriptions")

    # ── الوصول والعزل ──────────────────────────────────────────────
    def test_only_an_executive_director_reaches_the_screen(self):
        stranger = Teacher.objects.create_user(
            phone="500990033", name="غريب", password="pass"
        )
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_schools_outside_his_group_never_appear(self):
        self.client.force_login(self.director)
        body = self.client.get(self.url).content.decode()

        self.assertIn(self.healthy.name, body)
        self.assertNotIn(self.outsider.name, body)

    # ── الترتيب والمحتوى ───────────────────────────────────────────
    def test_what_needs_action_is_listed_before_what_is_settled(self):
        self.client.force_login(self.director)
        rows = self.client.get(self.url).context["rows"]

        self.assertEqual(
            [row["school"].name for row in rows],
            [self.bare.name, self.urgent.name, self.healthy.name],
        )

    def test_a_school_without_a_subscription_is_shown_not_dropped(self):
        self.client.force_login(self.director)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "بلا اشتراك")
        self.assertEqual(response.context["totals"]["stopped"], 1)
        self.assertEqual(response.context["totals"]["urgent"], 1)
        self.assertEqual(response.context["totals"]["active"], 1)

    def test_each_row_carries_whom_to_call(self):
        """تنبيهٌ بلا وجهةٍ للاتصال تنبيهٌ ناقص."""
        self.client.force_login(self.director)
        response = self.client.get(self.url)

        self.assertContains(response, self.manager.name)
        self.assertContains(response, f'href="tel:{self.manager.phone}"')

    def test_the_attention_filter_hides_the_settled_schools(self):
        self.client.force_login(self.director)
        rows = self.client.get(self.url, {"state": "attention"}).context["rows"]

        names = {row["school"].name for row in rows}
        self.assertIn(self.urgent.name, names)
        self.assertIn(self.bare.name, names)
        self.assertNotIn(self.healthy.name, names)

    # ── الدفع نيابةً يبدأ من هنا ────────────────────────────────────
    def test_every_row_opens_that_school_s_own_purchase_page(self):
        """الزر يحمل معرّف مدرسة صفّه لا مدرسةً أخرى.

        رابطٌ بلا معرّف كان سيفتح صفحة المدير التنفيذي نفسه — أي لا شيء —
        ورابطٌ بمعرّفٍ ثابت كان سيحصّل دفعة مدرسةٍ على أخرى.
        """
        self.client.force_login(self.director)
        body = self.client.get(self.url).content.decode()

        base = reverse("reports:my_subscription")
        for school in (self.healthy, self.urgent, self.bare):
            with self.subTest(school=school.name):
                self.assertIn(f'href="{base}?school={school.pk}"', body)
        self.assertNotIn(f'href="{base}?school={self.outsider.pk}"', body)

    def test_the_dashboard_points_at_the_new_screen(self):
        self.client.force_login(self.director)
        body = self.client.get(reverse("reports:executive_dashboard")).content.decode()

        self.assertIn(self.url, body)
