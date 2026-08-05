import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Payment,
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


def _notification_token(secret: str) -> str:
    def encoded(value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = encoded({"typ": "JWT", "alg": "HS256"})
    payload = encoded({"iss": "Tamara", "exp": int(time.time()) + 300})
    signature = hmac.new(secret.encode("utf-8"), f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


@override_settings(ALLOWED_HOSTS=["testserver"], RATELIMIT_ENABLE=False)
class TamaraPaymentTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="باقة سنوية",
            price=Decimal("1200.00"),
            days_duration=365,
            max_teachers=50,
        )
        self.school = School.objects.create(
            name="مدرسة تمارا",
            code="tamara-school",
            city="الرياض",
        )
        self.subscription = SchoolSubscription.objects.create(school=self.school, plan=self.plan)
        self.manager = Teacher.objects.create_user(
            phone="0550111222",
            name="مدير المدرسة",
            password="pass",
            email="manager@example.com",
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

    def _checkout_payload(self):
        return {
            "include_subscription": "1",
            "plan_id": str(self.plan.id),
            "tamara_city": "الرياض",
            "tamara_address": "حي الياسمين، شارع أنس بن مالك",
        }

    def test_tamara_option_is_hidden_when_disabled(self):
        response = self.client.get(reverse("reports:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="tamaraSubmit"')

    @override_settings(TAMARA_ENABLED=True, TAMARA_ENVIRONMENT="sandbox")
    def test_tamara_option_is_marked_as_sandbox(self):
        response = self.client.get(reverse("reports:my_subscription"))

        self.assertContains(response, 'id="tamaraSubmit"')
        self.assertContains(response, 'id="tamaraInstallmentAmount"')
        self.assertContains(response, "img/tamara-wordmark-gradient-ar.png")
        self.assertContains(response, "إضافة عنوان فوترة")
        self.assertContains(response, "اختياري")
        self.assertContains(response, "بيئة اختبار")

    @override_settings(TAMARA_ENABLED=True, TAMARA_API_TOKEN="sandbox-token")
    @patch("reports.views.subscriptions.is_customer_eligible", return_value=True)
    @patch("reports.views.subscriptions.create_checkout")
    def test_checkout_uses_server_price_and_creates_pending_gateway_payment(
        self, create_checkout_mock, eligibility_mock
    ):
        create_checkout_mock.return_value = {
            "order_id": "11111111-1111-1111-1111-111111111111",
            "checkout_id": "22222222-2222-2222-2222-222222222222",
            "status": "new",
            "checkout_url": "https://checkout.tamara.co/checkout/example",
        }

        response = self.client.post(reverse("reports:tamara_checkout_create"), self._checkout_payload())

        self.assertRedirects(
            response,
            "https://checkout.tamara.co/checkout/example",
            fetch_redirect_response=False,
        )
        payment = Payment.objects.get(school=self.school)
        self.assertEqual(payment.payment_method, Payment.Method.TAMARA)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, self.plan.price)
        self.assertEqual(payment.gateway_status, "new")
        self.assertFalse(payment.receipt_image)
        sent_payload = create_checkout_mock.call_args.args[0]
        self.assertEqual(sent_payload["total_amount"]["amount"], 1200.0)
        self.assertEqual(sent_payload["tax_amount"]["amount"], 0.0)
        self.assertNotIn("discount", sent_payload)
        self.assertEqual(sent_payload["items"][0]["type"], "Subscription - Digital")
        self.assertEqual(sent_payload["risk_assessment"]["total_order_count"], 0)
        self.assertEqual(sent_payload["risk_assessment"]["education"]["purchase_type"], "Subscription")
        self.assertFalse(sent_payload["is_mobile"])
        self.assertNotIn("shipping_address", sent_payload)
        self.assertEqual(sent_payload["billing_address"]["city"], "الرياض")
        eligibility_mock.assert_called_once_with(
            amount=Decimal("1200.00"),
            phone=self.manager.phone,
            email=self.manager.email,
        )

    @override_settings(TAMARA_ENABLED=True, TAMARA_API_TOKEN="sandbox-token")
    @patch("reports.views.subscriptions.is_customer_eligible", return_value=True)
    @patch("reports.views.subscriptions.create_checkout")
    def test_checkout_accepts_missing_optional_billing_address(
        self, create_checkout_mock, eligibility_mock
    ):
        create_checkout_mock.return_value = {
            "order_id": "11111111-1111-1111-1111-111111111111",
            "checkout_id": "22222222-2222-2222-2222-222222222222",
            "status": "new",
            "checkout_url": "https://checkout.tamara.co/checkout/example",
        }
        payload = self._checkout_payload()
        payload.pop("tamara_city")
        payload.pop("tamara_address")

        response = self.client.post(reverse("reports:tamara_checkout_create"), payload)

        self.assertRedirects(
            response,
            "https://checkout.tamara.co/checkout/example",
            fetch_redirect_response=False,
        )
        sent_payload = create_checkout_mock.call_args.args[0]
        self.assertNotIn("shipping_address", sent_payload)
        self.assertNotIn("billing_address", sent_payload)
        eligibility_mock.assert_called_once()

    @override_settings(TAMARA_ENABLED=True, TAMARA_API_TOKEN="sandbox-token")
    @patch("reports.views.subscriptions.is_customer_eligible", return_value=False)
    @patch("reports.views.subscriptions.create_checkout")
    def test_ineligible_customer_does_not_create_checkout(self, create_checkout_mock, eligibility_mock):
        response = self.client.post(reverse("reports:tamara_checkout_create"), self._checkout_payload())

        self.assertRedirects(response, reverse("reports:my_subscription"), fetch_redirect_response=False)
        self.assertFalse(Payment.objects.exists())
        create_checkout_mock.assert_not_called()
        eligibility_mock.assert_called_once()

    @override_settings(TAMARA_ENABLED=True, TAMARA_API_TOKEN="sandbox-token")
    @patch("reports.views.subscriptions.get_order", return_value={"status": "new"})
    def test_manager_can_cancel_unpaid_tamara_batch(self, get_order_mock):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=self.plan.price,
            payment_method=Payment.Method.TAMARA,
            gateway_order_id="55555555-5555-5555-5555-555555555555",
            gateway_status="new",
            batch_ref="cancel-batch",
            created_by=self.manager,
        )

        response = self.client.post(reverse("reports:tamara_checkout_cancel", args=[payment.id]))

        self.assertRedirects(response, reverse("reports:my_subscription"), fetch_redirect_response=False)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(payment.gateway_status, "customer_cancelled")
        get_order_mock.assert_called_once_with(payment.gateway_order_id)

    @override_settings(TAMARA_ENABLED=True, TAMARA_API_TOKEN="sandbox-token")
    @patch("reports.views.subscriptions.is_customer_eligible", return_value=True)
    @patch("reports.views.subscriptions.create_checkout")
    def test_unsafe_checkout_url_creates_no_payment(self, create_checkout_mock, eligibility_mock):
        create_checkout_mock.return_value = {
            "order_id": "11111111-1111-1111-1111-111111111111",
            "checkout_id": "22222222-2222-2222-2222-222222222222",
            "status": "new",
            "checkout_url": "https://eviltamara.co/fake-checkout",
        }

        response = self.client.post(reverse("reports:tamara_checkout_create"), self._checkout_payload())

        self.assertRedirects(response, reverse("reports:my_subscription"), fetch_redirect_response=False)
        self.assertFalse(Payment.objects.exists())

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_webhook_rejects_invalid_notification_token(self):
        response = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps({"order_id": "unknown", "event_type": "order_approved"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid",
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_captured_webhook_fulfils_batch_once(self):
        order_id = "33333333-3333-3333-3333-333333333333"
        batch_ref = "abc123"
        subscription_payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref=batch_ref,
            created_by=self.manager,
        )
        archive_payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            purpose=Payment.Purpose.ARCHIVE_ADDON,
            amount=Decimal("399.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref=batch_ref,
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-ABC123",
            "event_type": "order_captured",
            "data": {
                "capture_id": "capture-1",
                "captured_amount": {"amount": 1599, "currency": "SAR"},
            },
        }
        token = _notification_token("notification-secret")

        first = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        addon = SchoolArchiveAddon.objects.get(school=self.school)
        first_end_date = addon.end_date
        second = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        subscription_payment.refresh_from_db()
        archive_payment.refresh_from_db()
        addon.refresh_from_db()
        self.assertEqual(subscription_payment.status, Payment.Status.APPROVED)
        self.assertEqual(archive_payment.status, Payment.Status.APPROVED)
        self.assertIsNotNone(subscription_payment.effects_applied_at)
        self.assertIsNotNone(archive_payment.effects_applied_at)
        self.assertEqual(addon.end_date, first_end_date)
        self.assertEqual(addon.paid_amount, Decimal("399.00"))

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_captured_subscription_reactivates_school_and_applies_requested_plan(self):
        replacement_plan = SubscriptionPlan.objects.create(
            name="باقة نصف سنوية",
            price=Decimal("800.00"),
            days_duration=180,
            max_teachers=25,
        )
        self.subscription.is_active = False
        self.subscription.end_date = timezone.localdate() - timedelta(days=5)
        self.subscription.canceled_at = timezone.now()
        self.subscription.cancel_reason = "إلغاء سابق"
        self.subscription.save(
            update_fields=["is_active", "end_date", "canceled_at", "cancel_reason", "updated_at"]
        )
        order_id = "77777777-7777-7777-7777-777777777777"
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=replacement_plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=replacement_plan.price,
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref="reactivate",
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-REACTIVATE",
            "event_type": "order_captured",
            "data": {
                "capture_id": "capture-reactivate",
                "captured_amount": {"amount": 800, "currency": "SAR"},
            },
        }
        self.client.logout()

        response = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_notification_token('notification-secret')}",
        )

        self.assertEqual(response.status_code, 200)
        self.subscription.refresh_from_db()
        payment.refresh_from_db()
        self.assertTrue(self.subscription.is_active)
        self.assertEqual(self.subscription.plan, replacement_plan)
        self.assertEqual(self.subscription.start_date, timezone.localdate())
        self.assertEqual(
            self.subscription.end_date,
            timezone.localdate() + timedelta(days=replacement_plan.days_duration - 1),
        )
        self.assertIsNone(self.subscription.canceled_at)
        self.assertEqual(self.subscription.cancel_reason, "")
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertIsNotNone(payment.effects_applied_at)

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_captured_subscription_creates_missing_school_subscription(self):
        self.subscription.delete()
        order_id = "88888888-8888-8888-8888-888888888888"
        payment = Payment.objects.create(
            school=self.school,
            subscription=None,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=self.plan.price,
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref="create-sub",
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-CREATE-SUB",
            "event_type": "order_captured",
            "data": {
                "capture_id": "capture-create-sub",
                "captured_amount": {"amount": 1200, "currency": "SAR"},
            },
        }
        self.client.logout()

        response = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_notification_token('notification-secret')}",
        )

        self.assertEqual(response.status_code, 200)
        subscription = SchoolSubscription.objects.get(school=self.school)
        payment.refresh_from_db()
        self.assertTrue(subscription.is_active)
        self.assertEqual(subscription.plan, self.plan)
        self.assertEqual(payment.subscription, subscription)
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertIsNotNone(payment.effects_applied_at)

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_captured_subscription_without_plan_is_not_silently_approved(self):
        self.subscription.delete()
        order_id = "99999999-9999-9999-9999-999999999999"
        payment = Payment.objects.create(
            school=self.school,
            subscription=None,
            requested_plan=None,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref="missing-plan",
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-MISSING-PLAN",
            "event_type": "order_captured",
            "data": {
                "capture_id": "capture-missing-plan",
                "captured_amount": {"amount": 1200, "currency": "SAR"},
            },
        }
        self.client.logout()

        response = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_notification_token('notification-secret')}",
        )

        self.assertEqual(response.status_code, 502)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.effects_applied_at)
        self.assertFalse(SchoolSubscription.objects.filter(school=self.school).exists())

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_capture_amount_mismatch_does_not_fulfil_order(self):
        order_id = "44444444-4444-4444-4444-444444444444"
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_status="authorised",
            batch_ref="mismatch",
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-MISMATCH",
            "event_type": "order_captured",
            "data": {
                "capture_id": "capture-short",
                "captured_amount": {"amount": 100, "currency": "SAR"},
            },
        }

        response = self.client.post(
            reverse("reports:tamara_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {_notification_token('notification-secret')}",
        )

        self.assertEqual(response.status_code, 502)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.effects_applied_at)

    @override_settings(TAMARA_ENABLED=True, TAMARA_NOTIFICATION_TOKEN="notification-secret")
    def test_refund_webhook_records_negative_payment_once(self):
        order_id = "66666666-6666-6666-6666-666666666666"
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id=order_id,
            gateway_capture_id="capture-6",
            gateway_status="fully_captured",
            batch_ref="refund-test",
            status=Payment.Status.APPROVED,
            created_by=self.manager,
        )
        payload = {
            "order_id": order_id,
            "order_reference_id": "TWQ-REFUND-TEST",
            "event_type": "order_refunded",
            "data": {
                "refund_id": "refund-6",
                "capture_id": "capture-6",
                "refunded_amount": {"amount": 300, "currency": "SAR"},
            },
        }
        token = _notification_token("notification-secret")

        for _ in range(2):
            response = self.client.post(
                reverse("reports:tamara_webhook"),
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
            self.assertEqual(response.status_code, 200)

        payment.refresh_from_db()
        refund = Payment.objects.get(gateway_capture_id="refund-6")
        self.assertEqual(payment.gateway_status, "partially_refunded")
        self.assertEqual(refund.amount, Decimal("-300.00"))
        self.assertEqual(refund.status, Payment.Status.APPROVED)

    @override_settings(TAMARA_ENABLED=True)
    def test_platform_admin_cannot_approve_uncaptured_tamara_payment(self):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.TAMARA,
            gateway_order_id="55555555-5555-5555-5555-555555555555",
            gateway_status="approved",
            batch_ref="manual-block",
            created_by=self.manager,
        )
        admin = Teacher.objects.create_superuser(
            phone="0550999888",
            name="مدير المنصة",
            password="pass",
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse("reports:platform_payment_detail", args=[payment.id]),
            {"status": Payment.Status.APPROVED},
        )

        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.effects_applied_at)
