from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import CustomerComplaint


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    RATELIMIT_ENABLE=False,
    SITE_URL="https://tawtheeq.example",
    BUSINESS_LEGAL_NAME="شركة توثيق الاختبارية",
    BUSINESS_COMMERCIAL_REGISTRATION="1010123456",
    BUSINESS_TAX_NUMBER="310123456700003",
    BUSINESS_ADDRESS="الرياض، المملكة العربية السعودية",
    BUSINESS_SUPPORT_EMAIL="care@example.test",
    BUSINESS_SUPPORT_PHONE="+966500000000",
)
class LegalPagesTests(TestCase):
    def test_legal_pages_are_public_and_show_business_contact(self):
        routes = (
            "reports:terms_conditions",
            "reports:privacy_policy",
            "reports:refund_policy",
            "reports:service_delivery_policy",
            "reports:complaints_policy",
        )
        for route_name in routes:
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Robots-Tag", response.headers)
            self.assertContains(response, "شركة توثيق الاختبارية")
            self.assertContains(response, "care@example.test")
            self.assertContains(response, "1010123456")

    def test_landing_discloses_business_identity_and_policy_links(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertContains(response, "شركة توثيق الاختبارية")
        self.assertContains(response, "1010123456")
        self.assertContains(response, "310123456700003")
        self.assertContains(response, reverse("reports:terms_conditions"))
        self.assertContains(response, reverse("reports:refund_policy"))
        self.assertContains(response, reverse("reports:complaints_policy"))

    def test_public_complaint_form_creates_trackable_record(self):
        response = self.client.post(
            reverse("reports:complaints_policy"),
            {
                "name": "عميل تجريبي",
                "email": "customer@example.test",
                "phone": "0500000000",
                "order_reference": "ORDER-42",
                "subject": "تعذر تفعيل الاشتراك",
                "message": "تم الدفع ولم يظهر التفعيل حتى الآن.",
                "website": "",
            },
        )

        complaint = CustomerComplaint.objects.get()
        self.assertEqual(complaint.status, CustomerComplaint.Status.NEW)
        self.assertEqual(complaint.order_reference, "ORDER-42")
        self.assertRedirects(
            response,
            f"{reverse('reports:complaints_policy')}?submitted={complaint.reference}",
            fetch_redirect_response=False,
        )

        confirmation = self.client.get(response["Location"])
        self.assertContains(confirmation, complaint.reference)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    BUSINESS_LEGAL_NAME="ممارس عمل حر",
    BUSINESS_COMMERCIAL_REGISTRATION="",
    BUSINESS_FREELANCE_DOCUMENT_NUMBER="FL-12345678",
    BUSINESS_FREELANCE_ACTIVITY="تطوير المواقع والتطبيقات",
    BUSINESS_FREELANCE_DOCUMENT_EXPIRY="2027-06-26",
    BUSINESS_FREELANCE_DOCUMENT_URL="https://freelance.example.test/verify",
    BUSINESS_SUPPORT_EMAIL="care@example.test",
    BUSINESS_SUPPORT_PHONE="+966500000000",
)
class FreelanceBusinessDisclosureTests(TestCase):
    def test_freelance_document_replaces_commercial_registration(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertContains(response, "وثيقة العمل الحر")
        self.assertContains(response, "FL-12345678")
        self.assertContains(response, "تطوير المواقع والتطبيقات")
        self.assertContains(response, "2027-06-26")
        self.assertContains(response, "https://freelance.example.test/verify")
        self.assertNotContains(response, "السجل التجاري")
