from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.billing_invoices import build_invoice_context
from reports.models import (
    Payment,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.pdf_invoice import _generate_invoice_pdf_fallback


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    RATELIMIT_ENABLE=False,
    BUSINESS_LEGAL_NAME="مؤسسة منصة توثيق",
    BUSINESS_COMMERCIAL_REGISTRATION="1010101010",
    BUSINESS_ADDRESS="الرياض، المملكة العربية السعودية",
    BUSINESS_SUPPORT_EMAIL="support@example.com",
    BUSINESS_SUPPORT_PHONE="0500000000",
)
class BillingInvoiceTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="باقة القيادة السنوية",
            price=Decimal("1200.00"),
            days_duration=365,
            max_teachers=50,
        )
        self.school = School.objects.create(
            name="مدرسة الفاتورة",
            code="invoice-school",
            city="الرياض",
            email="school@example.com",
            phone="0551112233",
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
        )
        self.manager = Teacher.objects.create_user(
            phone="0559001001",
            name="مدير مدرسة الفاتورة",
            password="pass",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.subscription_payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            requested_teacher_limit=50,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.MOYASAR,
            status=Payment.Status.APPROVED,
            batch_ref="invoicebatch01",
            gateway_capture_id="capture-001",
            created_by=self.manager,
        )
        self.archive_payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            purpose=Payment.Purpose.ARCHIVE_ADDON,
            amount=Decimal("399.00"),
            payment_method=Payment.Method.MOYASAR,
            status=Payment.Status.APPROVED,
            batch_ref="invoicebatch01",
            gateway_capture_id="capture-001",
            created_by=self.manager,
        )
        self.pending_payment = Payment.objects.create(
            school=self.school,
            subscription=self.subscription,
            requested_plan=self.plan,
            purpose=Payment.Purpose.SUBSCRIPTION,
            amount=Decimal("1200.00"),
            payment_method=Payment.Method.BANK_TRANSFER,
            status=Payment.Status.PENDING,
            created_by=self.manager,
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_subscription_page_offers_view_and_pdf_for_approved_payment_only(self):
        response = self.client.get(reverse("reports:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "عرض الفاتورة")
        self.assertContains(
            response,
            reverse(
                "reports:subscription_invoice",
                args=[self.subscription_payment.pk],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "reports:subscription_invoice_pdf",
                args=[self.subscription_payment.pk],
            ),
        )
        self.assertContains(response, "عرض الفاتورة", count=1)

    def test_invoice_view_groups_the_unified_order(self):
        response = self.client.get(
            reverse("reports:subscription_invoice", args=[self.archive_payment.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], Decimal("1599.00"))
        self.assertEqual(len(response.context["items"]), 2)
        self.assertContains(response, "باقة القيادة السنوية")
        self.assertContains(response, "خدمة الأرشفة السنوية")
        self.assertContains(response, "مؤسسة منصة توثيق")
        self.assertContains(response, "INVOICEBATCH01", html=False)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_pending_payment_has_no_final_invoice(self):
        response = self.client.get(
            reverse("reports:subscription_invoice", args=[self.pending_payment.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_manager_cannot_read_another_school_invoice(self):
        other_school = School.objects.create(
            name="مدرسة أخرى", code="other-invoice-school"
        )
        SchoolSubscription.objects.create(school=other_school, plan=self.plan)
        SchoolMembership.objects.create(
            school=other_school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        session = self.client.session
        session["active_school_id"] = other_school.pk
        session.save()

        response = self.client.get(
            reverse(
                "reports:subscription_invoice",
                args=[self.subscription_payment.pk],
            )
        )

        self.assertEqual(response.status_code, 404)

    @patch("reports.views.billing_school.generate_invoice_pdf")
    def test_pdf_download_uses_attachment_response(self, generate_pdf_mock):
        generate_pdf_mock.return_value = (
            b"%PDF-1.7\nmock",
            "tawtheeq-invoice-TQ-2026-TEST.pdf",
        )

        response = self.client.get(
            reverse(
                "reports:subscription_invoice_pdf",
                args=[self.subscription_payment.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response.content[:4], b"%PDF")
        generate_pdf_mock.assert_called_once()

    def test_reportlab_fallback_generates_a_valid_arabic_pdf(self):
        context = build_invoice_context(self.subscription_payment)

        pdf_bytes = _generate_invoice_pdf_fallback(context)

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 5000)
