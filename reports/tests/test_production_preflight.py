"""The preflight command must be trustworthy in both directions.

A readiness check that cannot fail is worse than none: it converts an unverified
deploy into a confident one. These tests prove each critical check actually
trips when the condition is wrong.
"""
from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from reports.models import SubscriptionPlan
from reports.pricing import DEFAULT_SUBSCRIPTION_PLANS


def run_preflight():
    out = StringIO()
    try:
        call_command("production_preflight", stdout=out, stderr=out)
        exit_code = 0
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    return exit_code, out.getvalue()


class PreflightRunsTests(TestCase):
    def test_it_completes_and_reports_a_verdict(self):
        exit_code, output = run_preflight()

        self.assertIn("Production preflight", output)
        self.assertIn("passed", output)
        self.assertIn(exit_code, (0, 1))

    def test_a_development_configuration_is_reported_as_not_ready(self):
        """The test settings are not production settings, and the command must
        say so rather than wave them through."""
        exit_code, output = run_preflight()

        self.assertEqual(exit_code, 1)
        self.assertIn("NOT READY", output)

    def test_no_check_crashes_the_run(self):
        _, output = run_preflight()

        self.assertNotIn("crashed", output)


class PreflightDetectsMisconfigurationTests(TestCase):
    @override_settings(DEBUG=True)
    def test_debug_on_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("DEBUG is on", output)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_wildcard_allowed_hosts_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("ALLOWED_HOSTS is unsafe", output)

    @override_settings(SECRET_KEY="aaaa")
    def test_a_weak_secret_key_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("SECRET_KEY is weak", output)

    @override_settings(MEDIA_PUBLIC_ACCESS_ENABLED=True)
    def test_public_media_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("MEDIA_PUBLIC_ACCESS_ENABLED is on", output)

    @override_settings(PAYMENT_RECONCILIATION_ENABLED=False)
    def test_disabled_payment_reconciliation_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("Payment reconciliation is off", output)

    @override_settings(CELERY_BEAT_SCHEDULE={})
    def test_missing_scheduled_jobs_are_named(self):
        _, output = run_preflight()

        self.assertIn("reconcile-pending-gateway-payments is not scheduled", output)
        self.assertIn("cleanup-expired-sessions-daily is not scheduled", output)

    @override_settings(
        MOYASAR_ENABLED=True,
        MOYASAR_ENVIRONMENT="test",
        MOYASAR_SECRET_KEY="sk_test_x",
    )
    def test_a_test_gateway_in_production_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("Moyasar enabled but environment='test'", output)

    @override_settings(
        BUSINESS_LEGAL_NAME="",
        BUSINESS_ADDRESS="",
        BUSINESS_SUPPORT_EMAIL="",
        BUSINESS_SUPPORT_PHONE="",
        BUSINESS_COMMERCIAL_REGISTRATION="",
        BUSINESS_FREELANCE_DOCUMENT_NUMBER="",
    )
    def test_missing_business_disclosure_is_a_failure(self):
        _, output = run_preflight()

        self.assertIn("Incomplete public disclosure", output)


class PreflightPricingChecksTests(TestCase):
    def setUp(self):
        for spec in DEFAULT_SUBSCRIPTION_PLANS:
            SubscriptionPlan.objects.create(is_active=True, **spec)

    def test_consistent_anchors_pass(self):
        _, output = run_preflight()

        self.assertIn("All paid anchors share one entitlement set", output)

    def test_an_entitlement_step_between_anchors_is_caught(self):
        plan = SubscriptionPlan.objects.filter(price__gt=0).order_by("-price").first()
        plan.included_archive_storage_gb = 50
        plan.save(update_fields=["included_archive_storage_gb"])

        _, output = run_preflight()

        self.assertIn("Paid anchors carry different entitlements", output)

    def test_a_downhill_price_curve_is_caught(self):
        plan = SubscriptionPlan.objects.get(days_duration=365, max_teachers=100)
        plan.price = 1
        plan.save(update_fields=["price"])

        _, output = run_preflight()

        self.assertIn("Price curve goes downhill", output)

    def test_no_published_paid_plan_is_a_failure(self):
        SubscriptionPlan.objects.update(is_active=False)

        _, output = run_preflight()

        self.assertIn("No active paid plan is published", output)
