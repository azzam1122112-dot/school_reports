"""An unpaid electronic order must not sit pending forever.

A hosted checkout URL is single-use and is not stored, so a customer who closes
that tab cannot get back to it. Before this, the order stayed "بانتظار إكمال
الدفع الإلكتروني" indefinitely: the school could not finish it, could not clear
it, and add-on purchases that refuse to queue behind a pending request stayed
blocked too.

Two ways out are covered here — the manager cancelling it, and the
reconciliation sweep dropping it once it is clearly abandoned — plus the thing
both must never do: cancel an order the customer actually paid.
"""

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
from reports.views.subscriptions import reconcile_pending_gateway_payments


@override_settings(ALLOWED_HOSTS=["testserver"], MOYASAR_ENABLED=True)
class AbandonedMoyasarOrderTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة الدفع", code="abandoned-pay")
        self.plan = SubscriptionPlan.objects.create(
            name="باقة الدفع",
            price=Decimal("1290.00"),
            days_duration=365,
            max_teachers=25,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school, plan=self.plan
        )
        self.manager = Teacher.objects.create_user(
            phone="0500770011",
            name="مدير المدرسة",
            password="manager-safe-password",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _pending_payment(self, *, age_minutes=0, batch="batch-abandoned"):
        payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1290.00"),
            status=Payment.Status.PENDING,
            payment_method=Payment.Method.MOYASAR,
            batch_ref=batch,
            gateway_order_id=f"inv-{batch}",
            gateway_status="initiated",
            created_by=self.manager,
        )
        if age_minutes:
            Payment.objects.filter(pk=payment.pk).update(
                created_at=timezone.now() - timedelta(minutes=age_minutes)
            )
            payment.refresh_from_db()
        return payment

    # ── the manager clears it themselves ──────────────────────────────────
    def test_manager_can_cancel_an_order_the_gateway_still_reports_unpaid(self):
        payment = self._pending_payment()

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "initiated"},
        ):
            response = self.client.post(
                reverse("reports:moyasar_checkout_cancel", args=[payment.pk])
            )

        self.assertRedirects(response, reverse("reports:my_subscription"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)

    def test_cancelling_a_paid_order_is_refused_and_completes_it_instead(self):
        """The callback may simply be late — money already taken must not vanish."""
        payment = self._pending_payment(batch="batch-paid")

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "paid"},
        ), patch("reports.views.subscriptions._complete_moyasar_invoice") as complete:
            self.client.post(
                reverse("reports:moyasar_checkout_cancel", args=[payment.pk])
            )

        complete.assert_called_once()
        payment.refresh_from_db()
        self.assertNotEqual(payment.status, Payment.Status.CANCELLED)

    def test_a_manager_cannot_cancel_another_schools_order(self):
        other = School.objects.create(name="مدرسة أخرى", code="other-school")
        foreign = Payment.objects.create(
            school=other,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("149.00"),
            status=Payment.Status.PENDING,
            payment_method=Payment.Method.MOYASAR,
            batch_ref="batch-foreign",
            gateway_order_id="inv-foreign",
            created_by=self.manager,
        )

        self.client.post(reverse("reports:moyasar_checkout_cancel", args=[foreign.pk]))

        foreign.refresh_from_db()
        self.assertEqual(foreign.status, Payment.Status.PENDING)

    # ── the sweep clears it without anyone clicking ───────────────────────
    def test_the_sweep_cancels_an_order_left_unpaid_past_the_limit(self):
        payment = self._pending_payment(age_minutes=120, batch="batch-stale")

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "initiated"},
        ):
            summary = reconcile_pending_gateway_payments(abandon_after_minutes=60)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CANCELLED)
        self.assertEqual(payment.gateway_status, "abandoned")
        self.assertEqual(summary["abandoned"], 1)

    def test_a_fresh_order_is_left_alone_so_a_paying_customer_is_not_cut_off(self):
        payment = self._pending_payment(age_minutes=5, batch="batch-fresh")

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "initiated"},
        ):
            summary = reconcile_pending_gateway_payments(abandon_after_minutes=60)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(summary["abandoned"], 0)
        self.assertEqual(summary["still_pending"], 1)

    def test_a_paid_order_is_activated_by_the_sweep_never_abandoned(self):
        payment = self._pending_payment(age_minutes=600, batch="batch-late-paid")

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "paid"},
        ), patch("reports.views.subscriptions._complete_moyasar_invoice") as complete:
            summary = reconcile_pending_gateway_payments(abandon_after_minutes=60)

        complete.assert_called_once()
        self.assertEqual(summary["abandoned"], 0)
        self.assertEqual(summary["activated"], 1)

    def test_abandonment_can_be_switched_off(self):
        payment = self._pending_payment(age_minutes=600, batch="batch-disabled")

        with patch(
            "reports.views.subscriptions.fetch_moyasar_invoice",
            return_value={"id": payment.gateway_order_id, "status": "initiated"},
        ):
            summary = reconcile_pending_gateway_payments(abandon_after_minutes=0)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(summary["abandoned"], 0)
