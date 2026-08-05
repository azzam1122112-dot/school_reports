"""A payment brand mark is a claim that we accept that method.

Tamara is integrated but not activated yet (``TAMARA_ENABLED`` defaults to
False), so its wordmark must not appear anywhere a visitor can reach. These
tests pin the mark to the gateway switch in both directions, so enabling the
gateway later brings the logo back without another code change.
"""

from django.test import TestCase, override_settings
from django.urls import reverse


TAMARA_LOGO = "tamara-wordmark"
TAMARA_WORD = "تمارا"

# Pages a signed-out visitor can reach that carry payment branding.
PUBLIC_PAGES = ("reports:landing", "reports:user_guide", "reports:faq")


@override_settings(ALLOWED_HOSTS=["testserver"])
class TamaraBrandMarkIsGatedTests(TestCase):
    def _bodies(self):
        for name in PUBLIC_PAGES:
            response = self.client.get(reverse(name), follow=True)
            self.assertEqual(response.status_code, 200, name)
            yield name, response.content.decode("utf-8", errors="replace")

    @override_settings(TAMARA_ENABLED=False)
    def test_no_public_page_shows_the_tamara_mark_while_the_gateway_is_off(self):
        offenders = []
        for name, body in self._bodies():
            if TAMARA_LOGO in body:
                offenders.append(f"{name}: logo")
            if TAMARA_WORD in body:
                offenders.append(f"{name}: wordmark text")

        self.assertEqual(
            offenders,
            [],
            "تمارا غير مفعّلة، فلا يجوز أن يظهر شعارها أو اسمها:\n" + "\n".join(offenders),
        )

    @override_settings(TAMARA_ENABLED=True)
    def test_landing_shows_the_tamara_mark_once_the_gateway_is_on(self):
        response = self.client.get(reverse("reports:landing"), follow=True)
        body = response.content.decode("utf-8", errors="replace")

        self.assertIn(TAMARA_LOGO, body)
        self.assertIn(TAMARA_WORD, body)
