from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CSRF_FAILURE_VIEW="core.views.csrf_failure",
)
class LoginCsrfFailureTests(TestCase):
    def setUp(self):
        self.csrf_client = Client(enforce_csrf_checks=True)

    def test_stale_login_form_redirects_to_fresh_form_with_safe_next(self):
        response = self.csrf_client.post(
            reverse("reports:login"),
            {
                "phone": "0500000000",
                "password": "not-used",
                "next": "/support/new/",
            },
        )

        self.assertRedirects(
            response,
            "/login/?next=%2Fsupport%2Fnew%2F",
            fetch_redirect_response=False,
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        refreshed = self.csrf_client.get(response.headers["Location"])
        self.assertContains(refreshed, "انتهت صلاحية صفحة الدخول")
        self.assertContains(refreshed, 'name="csrfmiddlewaretoken"')

    def test_stale_login_form_drops_external_next_url(self):
        response = self.csrf_client.post(
            reverse("reports:login"),
            {
                "phone": "0500000000",
                "password": "not-used",
                "next": "https://example.com/steal-session",
            },
        )

        self.assertRedirects(response, "/login/", fetch_redirect_response=False)

    def test_non_login_csrf_failure_remains_forbidden(self):
        response = self.csrf_client.post(
            reverse("reports:password_reset"),
            {"email": "user@example.com"},
        )

        self.assertEqual(response.status_code, 403)
