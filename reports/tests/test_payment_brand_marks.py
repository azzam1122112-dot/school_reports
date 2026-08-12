"""A payment brand mark is a claim that we accept that method.

Tamara was removed from the platform entirely — gateway module, routes,
settings, wordmark and payment-method choice. This file used to gate the mark
behind ``TAMARA_ENABLED``; it now pins the stronger invariant: the name and the
logo must not reappear on any page, public or behind login. Re-adding the
integration should be a deliberate act that fails this test first.

**لماذا تُعدّ الصفحات كلها لا صفحة الهبوط وحدها؟** النسخة الأولى من هذا
الاختبار غطّت ثلاث صفحات عامة، وكانت سياسة الخصوصية — وهي عامة كذلك — تسمّي
البوابة اسماً صريحاً غير مشروط بأي عَلَم، فمرّ الذكرُ من ثغرة التغطية لا من
ثغرة المنطق. فالقائمة هنا تشمل ما يراه الزائر وما يراه المشترك بعد الدخول:
صفحة الاشتراك وسجلّ المدفوعات.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


TAMARA_LOGO = "tamara-wordmark"
TAMARA_WORD = "تمارا"

# كل ما يبلغه زائر غير مسجَّل ويحمل — أو قد يحمل — ذكراً لوسيلة دفع.
PUBLIC_PAGES = (
    "reports:landing",
    "reports:user_guide",
    "reports:faq",
    "reports:privacy_policy",
    "reports:terms_conditions",
    "reports:refund_policy",
    "reports:service_delivery_policy",
    "reports:complaints_policy",
)

# وما يبلغه المشترك بعد الدخول: هنا يقع اختيار وسيلة الدفع فعلاً.
MEMBER_PAGES = (
    "reports:my_subscription",
    "reports:subscription_history",
)


def _assert_clean(testcase, bodies):
    offenders = []
    for name, body in bodies:
        if TAMARA_LOGO in body:
            offenders.append(f"{name}: logo")
        if TAMARA_WORD in body:
            offenders.append(f"{name}: wordmark text")

    testcase.assertEqual(
        offenders,
        [],
        "تمارا محذوفة من المنصة، فلا يجوز أن يظهر شعارها أو اسمها:\n"
        + "\n".join(offenders),
    )


@override_settings(ALLOWED_HOSTS=["testserver"])
class TamaraIsGoneFromPublicPagesTests(TestCase):
    def _bodies(self):
        for name in PUBLIC_PAGES:
            response = self.client.get(reverse(name), follow=True)
            self.assertEqual(response.status_code, 200, name)
            yield name, response.content.decode("utf-8", errors="replace")

    def test_no_public_page_mentions_the_removed_gateway(self):
        _assert_clean(self, self._bodies())

    @override_settings(MOYASAR_ENABLED=True)
    def test_the_privacy_policy_names_only_the_gateways_actually_in_use(self):
        """الإفصاح عن معالِج بيانات لا نستعمله خطأٌ نظامي لا تفصيل واجهة."""
        body = self.client.get(
            reverse("reports:privacy_policy"), follow=True
        ).content.decode("utf-8", errors="replace")

        self.assertNotIn(TAMARA_WORD, body)
        self.assertIn("ميسر", body)


@override_settings(ALLOWED_HOSTS=["testserver"])
class TamaraIsGoneFromMemberPagesTests(TestCase):
    """ما يراه المشترك بعد الدخول يخضع للقاعدة نفسها.

    اختيار وسيلة الدفع يقع في «اشتراكي»، وسجلّ المدفوعات يعرض حالة البوابة —
    وكلاهما خلف تسجيل دخول، فلا يبلغهما اختبار الصفحات العامة.
    """

    def setUp(self):
        self.school = School.objects.create(name="مدرسة الدفع", code="pay-school")
        plan = SubscriptionPlan.objects.create(
            name="باقة", price=100, days_duration=365, max_teachers=25
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.manager = Teacher.objects.create_user(
            phone="500770001", name="مدير", password="pass"
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

    @override_settings(MOYASAR_ENABLED=True)
    def test_no_member_page_mentions_the_removed_gateway(self):
        def bodies():
            for name in MEMBER_PAGES:
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200, name)
                yield name, response.content.decode("utf-8", errors="replace")

        _assert_clean(self, bodies())

    @override_settings(MOYASAR_ENABLED=True)
    def test_the_electronic_payment_option_is_still_offered_through_moyasar(self):
        """حذف تمارا لا يجوز أن يُخفي الدفع الإلكتروني نفسه."""
        body = self.client.get(
            reverse("reports:my_subscription"), follow=True
        ).content.decode("utf-8", errors="replace")

        self.assertIn("paymentPanelMoyasar", body)
        self.assertNotIn("paymentPanelTamara", body)
