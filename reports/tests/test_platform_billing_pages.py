from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import Payment, School, SchoolSubscription, SubscriptionPlan, Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlatformBillingPagesTests(TestCase):
    def setUp(self):
        self.admin = Teacher.objects.create_superuser(
            phone="599000001",
            name="Platform Admin",
            password="pass",
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Annual",
            price=1200,
            days_duration=365,
            max_teachers=0,
        )
        self.school = School.objects.create(
            name="Revenue School",
            code="revenue-school",
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
        )
        Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            amount=1200,
            payment_date=timezone.localdate(),
            status=Payment.Status.APPROVED,
            notes="Approved payment",
            created_by=self.admin,
        )
        Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            amount=1200,
            payment_date=timezone.localdate(),
            status=Payment.Status.PENDING,
            notes="Pending payment",
            created_by=self.admin,
        )
        self.client.force_login(self.admin)

    def test_platform_subscriptions_list_renders_commercial_summary(self):
        response = self.client.get(reverse("reports:platform_subscriptions_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "قيمة الاشتراكات السارية")
        self.assertContains(response, "محفظة الاشتراكات")
        self.assertContains(response, self.school.name)

    def test_platform_payments_list_renders_financial_summary_and_search(self):
        response = self.client.get(
            reverse("reports:platform_payments_list"),
            data={"q": "Revenue", "status": "active"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "صافي الإيراد")
        self.assertContains(response, "عمليات التحصيل")
        self.assertContains(response, self.school.name)

    def test_platform_detail_pages_render_decision_blocks(self):
        subscription_response = self.client.get(
            reverse("reports:platform_subscription_detail", args=[self.subscription.id])
        )
        payment = Payment.objects.filter(status=Payment.Status.PENDING).first()
        payment_response = self.client.get(
            reverse("reports:platform_payment_detail", args=[payment.id])
        )

        self.assertEqual(subscription_response.status_code, 200)
        self.assertContains(subscription_response, "ملخص مالي سريع")
        self.assertEqual(payment_response.status_code, 200)
        self.assertContains(payment_response, "ملخص القرار المالي")
