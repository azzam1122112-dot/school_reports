"""A hosted checkout is reached by redirecting the payment form off-site.

Browsers enforce ``form-action`` across the *whole redirect chain*, not just
the form's own target. So if an enabled gateway's checkout origin is missing
from the directive, the browser silently cancels the navigation: the order is
created server-side, the user is charged nothing, and the page just sits there.
That is exactly how Moyasar payments were failing in production while the
directive carried only a different, disabled gateway's origin.

These tests pin the invariant: enabled gateway ⇒ its origin is in form-action.
"""

from django.test import RequestFactory, SimpleTestCase, override_settings

from reports.middleware import ContentSecurityPolicyMiddleware


MOYASAR_ORIGIN = "https://checkout.moyasar.com"


def _form_action(path: str = "/subscription/my/") -> str:
    middleware = ContentSecurityPolicyMiddleware(lambda request: None)
    request = RequestFactory().get(path)
    request.csp_nonce = "test-nonce"
    policy = middleware._policy_for_request(request)
    for directive in policy.split(";"):
        directive = directive.strip()
        if directive.startswith("form-action"):
            return directive
    return ""


class PaymentCheckoutFormActionTests(SimpleTestCase):
    @override_settings(MOYASAR_ENABLED=True)
    def test_enabled_moyasar_origin_is_allowed(self):
        self.assertIn(MOYASAR_ORIGIN, _form_action())

    @override_settings(MOYASAR_ENABLED=False)
    def test_no_gateway_origin_leaks_while_every_gateway_is_off(self):
        directive = _form_action()
        self.assertEqual(directive, "form-action 'self'")

    @override_settings(
        MOYASAR_ENABLED=True,
        CONTENT_SECURITY_POLICY="default-src 'self'; form-action 'self'",
    )
    def test_custom_policy_still_gets_the_enabled_gateway_origin(self):
        """An emergency CSP override must not silently break checkout."""
        self.assertIn(MOYASAR_ORIGIN, _form_action())

    @override_settings(
        MOYASAR_ENABLED=True,
        CONTENT_SECURITY_POLICY="default-src 'self'",
    )
    def test_custom_policy_without_form_action_gains_the_directive(self):
        directive = _form_action()
        self.assertIn("'self'", directive)
        self.assertIn(MOYASAR_ORIGIN, directive)

    @override_settings(MOYASAR_ENABLED=True)
    def test_every_declared_gateway_is_covered_by_the_directive(self):
        """Adding a gateway without its origin here must fail loudly."""
        directive = _form_action()
        for _setting, origin in ContentSecurityPolicyMiddleware.PAYMENT_CHECKOUT_ORIGINS:
            self.assertIn(origin, directive, origin)
