import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Payment,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.tap_payments import webhook_hashstring


TAP_TEST_SETTINGS = {
    "TAP_ENABLED": True,
    "TAP_SECRET_KEY": "sk_test_example_secret",
    "TAP_MERCHANT_ID": "merchant_test_123",
    "TAP_CURRENCY": "SAR",
    "TAP_SOURCE_ID": "src_all",
    "SITE_URL": "https://example.test",
}


@override_settings(**TAP_TEST_SETTINGS)
class TapPaymentFlowTests(TestCase):
    def setUp(self):
        self.manager = Teacher.objects.create_user(
            phone="0551234567",
            name="مدير المدرسة",
            email="manager@example.test",
            password="pass",
        )
        self.school = School.objects.create(name="مدرسة الاختبار", code="tap-school")
        SchoolMembership.objects.create(
            teacher=self.manager,
            school=self.school,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.current_plan = SubscriptionPlan.objects.create(
            name="الحالية",
            price=Decimal("499.00"),
            days_duration=180,
            is_active=True,
        )
        self.requested_plan = SubscriptionPlan.objects.create(
            name="السنوية",
            price=Decimal("849.00"),
            days_duration=365,
            is_active=True,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.current_plan,
            start_date=timezone.localdate() - timedelta(days=180),
            end_date=timezone.localdate() - timedelta(days=1),
            is_active=False,
        )
        self.client.force_login(self.manager)

    @patch("reports.views.subscriptions.tap_create_charge")
    def test_create_redirects_to_tap_hosted_checkout_without_receipt(self, create_charge):
        create_charge.return_value = {
            "id": "chg_test_12345678",
            "status": "INITIATED",
            "transaction": {"url": "https://pay.tap.company/checkout/example"},
        }

        response = self.client.post(
            reverse("reports:tap_payment_create"),
            {
                "include_subscription": "1",
                "plan_id": str(self.requested_plan.pk),
            },
        )

        self.assertRedirects(
            response,
            "https://pay.tap.company/checkout/example",
            fetch_redirect_response=False,
        )
        payment = Payment.objects.get()
        self.assertEqual(payment.payment_method, Payment.Method.TAP)
        self.assertEqual(payment.transaction_id, "chg_test_12345678")
        self.assertEqual(payment.gateway_status, "INITIATED")
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(bool(payment.receipt_image))

        payload = create_charge.call_args.args[0]
        self.assertEqual(payload["amount"], 849.0)
        self.assertEqual(payload["currency"], "SAR")
        self.assertEqual(payload["source"]["id"], "src_all")
        self.assertEqual(payload["merchant"]["id"], "merchant_test_123")
        self.assertEqual(
            payload["redirect"]["url"],
            "https://example.test/subscription/payment/tap/return/",
        )
        self.assertEqual(
            payload["post"]["url"],
            "https://example.test/subscription/payment/tap/webhook/",
        )
        self.assertNotIn("sk_test_example_secret", json.dumps(payload))

    def test_subscription_page_shows_tap_checkout_without_exposing_secret(self):
        response = self.client.get(reverse("reports:my_subscription"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الدفع الآن عبر Tap")
        self.assertNotContains(response, "sk_test_example_secret")

    @patch("reports.views.subscriptions.tap_retrieve_charge")
    def test_return_verifies_charge_and_activates_requested_plan(self, retrieve_charge):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.requested_plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("849.00"),
            batch_ref="a" * 32,
            payment_method=Payment.Method.TAP,
            transaction_id="chg_test_87654321",
            gateway_status="INITIATED",
            created_by=self.manager,
        )
        retrieve_charge.return_value = self._charge(
            charge_id=payment.transaction_id,
            batch_ref=payment.batch_ref,
            amount="849.00",
            status="CAPTURED",
        )

        response = self.client.get(
            reverse("reports:tap_payment_return"),
            {"tap_id": payment.transaction_id},
        )

        self.assertRedirects(response, reverse("reports:my_subscription"))
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertEqual(payment.gateway_status, "CAPTURED")
        self.assertIsNotNone(payment.effects_applied_at)
        self.assertIsNotNone(payment.gateway_completed_at)
        self.assertTrue(self.subscription.is_active)
        self.assertEqual(self.subscription.plan_id, self.requested_plan.pk)
        self.assertEqual(self.subscription.start_date, timezone.localdate())
        self.assertEqual(
            self.subscription.end_date,
            timezone.localdate() + timedelta(days=364),
        )

    @patch("reports.views.subscriptions.tap_retrieve_charge")
    def test_return_does_not_approve_amount_mismatch(self, retrieve_charge):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.requested_plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("849.00"),
            batch_ref="b" * 32,
            payment_method=Payment.Method.TAP,
            transaction_id="chg_test_11223344",
            gateway_status="INITIATED",
            created_by=self.manager,
        )
        retrieve_charge.return_value = self._charge(
            charge_id=payment.transaction_id,
            batch_ref=payment.batch_ref,
            amount="1.00",
            status="CAPTURED",
        )

        self.client.get(
            reverse("reports:tap_payment_return"),
            {"tap_id": payment.transaction_id},
        )

        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertIsNone(payment.effects_applied_at)
        self.assertFalse(self.subscription.is_active)
        self.assertEqual(self.subscription.plan_id, self.current_plan.pk)

    def test_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            reverse("reports:tap_payment_webhook"),
            data=json.dumps({"id": "chg_test_99887766"}),
            content_type="application/json",
            HTTP_HASHSTRING="0" * 64,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)

    def test_webhook_hash_matches_documented_hmac_shape(self):
        payload = self._charge(
            charge_id="chg_test_55667788",
            batch_ref="c" * 32,
            amount="10.00",
            status="CAPTURED",
        )
        to_hash = (
            "x_idchg_test_55667788"
            "x_amount10.00"
            "x_currencySAR"
            "x_gateway_referencegateway-1"
            "x_payment_referencepayment-1"
            "x_statusCAPTURED"
            "x_created1722337200000"
        )
        expected = hmac.new(
            b"sk_test_example_secret",
            to_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(webhook_hashstring(payload), expected)

    def test_valid_webhook_is_idempotent(self):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.requested_plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("849.00"),
            batch_ref="d" * 32,
            payment_method=Payment.Method.TAP,
            transaction_id="chg_test_44332211",
            gateway_status="INITIATED",
            created_by=self.manager,
        )
        payload = self._charge(
            charge_id=payment.transaction_id,
            batch_ref=payment.batch_ref,
            amount="849.00",
            status="CAPTURED",
        )
        signature = webhook_hashstring(payload)

        first = self.client.post(
            reverse("reports:tap_payment_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HASHSTRING=signature,
        )
        self.assertEqual(first.status_code, 200)
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        first_effect_time = payment.effects_applied_at
        first_end_date = self.subscription.end_date

        second = self.client.post(
            reverse("reports:tap_payment_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HASHSTRING=signature,
        )
        self.assertEqual(second.status_code, 200)
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.APPROVED)
        self.assertEqual(payment.effects_applied_at, first_effect_time)
        self.assertEqual(self.subscription.end_date, first_end_date)

    def _charge(self, *, charge_id, batch_ref, amount, status):
        return {
            "id": charge_id,
            "object": "charge",
            "live_mode": False,
            "status": status,
            "amount": float(Decimal(amount)),
            "currency": "SAR",
            "merchant": {"id": "merchant_test_123"},
            "reference": {
                "order": f"ord_{batch_ref}",
                "transaction": f"txn_{batch_ref}",
                "gateway": "gateway-1",
                "payment": "payment-1",
            },
            "transaction": {"created": "1722337200000"},
            "response": {"code": "000", "message": "Captured"},
        }
