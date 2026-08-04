"""Regressions that keep anonymous marketing traffic cheap to serve.

The landing page is the entry point for every campaign click. Anything it does
per visit — a session row, an extra query, an unbounded table — is multiplied by
the full visitor count, so these behaviours are pinned by tests.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.sessions.models import Session
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import SubscriptionPlan
from reports.tasks import cleanup_expired_sessions_task


@override_settings(ALLOWED_HOSTS=["testserver"], SITE_URL="https://tawtheeq.example")
class AnonymousLandingTrafficTests(TestCase):
    def test_plain_landing_visit_does_not_create_a_session(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Session.objects.count(), 0)
        self.assertNotIn("sessionid", response.cookies)

    def test_repeated_landing_visits_stay_session_free(self):
        for _ in range(5):
            self.client.get(reverse("reports:landing"))

        self.assertEqual(Session.objects.count(), 0)

    def test_campaign_visit_still_records_first_touch_attribution(self):
        self.client.get(
            reverse("reports:landing"),
            {"utm_source": "meta", "utm_campaign": "launch"},
            HTTP_REFERER="https://www.facebook.com/ads/example",
        )

        stored = self.client.session.get("_marketing_attribution") or {}
        self.assertEqual(stored.get("marketing_source"), "meta")
        self.assertEqual(stored.get("marketing_campaign"), "launch")
        self.assertEqual(stored.get("marketing_referrer"), "www.facebook.com")

    def test_first_touch_attribution_is_not_overwritten_by_later_campaigns(self):
        self.client.get(reverse("reports:landing"), {"utm_source": "meta"})
        self.client.get(reverse("reports:landing"), {"utm_source": "google"})

        stored = self.client.session.get("_marketing_attribution") or {}
        self.assertEqual(stored.get("marketing_source"), "meta")


class MarketingAttributionDefaultsTests(TestCase):
    def _fields_for(self, session_payload):
        from reports.marketing_attribution import school_marketing_fields

        request = self.client.request().wsgi_request
        request.session["_marketing_attribution"] = session_payload
        return school_marketing_fields(request)

    def test_visitor_without_attribution_is_reported_as_direct(self):
        fields = self._fields_for({})

        self.assertEqual(fields["marketing_source"], "direct")
        self.assertEqual(fields["marketing_medium"], "none")

    def test_visitor_with_only_a_referrer_is_reported_as_referral(self):
        fields = self._fields_for({"marketing_referrer": "www.facebook.com"})

        self.assertEqual(fields["marketing_source"], "referral")
        self.assertEqual(fields["marketing_medium"], "referral")
        self.assertEqual(fields["marketing_referrer"], "www.facebook.com")

    def test_campaign_attribution_is_returned_untouched(self):
        fields = self._fields_for(
            {"marketing_medium": "paid_social", "marketing_campaign": "launch"}
        )

        # A campaign without utm_source must stay blank rather than be
        # relabelled "direct".
        self.assertEqual(fields["marketing_source"], "")
        self.assertEqual(fields["marketing_medium"], "paid_social")
        self.assertEqual(fields["marketing_campaign"], "launch")


class ExpiredSessionCleanupTests(TestCase):
    def _make_session(self, *, expire_date):
        from django.contrib.sessions.backends.db import SessionStore

        store = SessionStore()
        store["marker"] = "x"
        store.create()
        Session.objects.filter(session_key=store.session_key).update(expire_date=expire_date)
        return store.session_key

    def test_expired_sessions_are_deleted_and_live_ones_kept(self):
        now = timezone.now()
        expired_key = self._make_session(expire_date=now - timedelta(days=1))
        live_key = self._make_session(expire_date=now + timedelta(days=1))

        deleted = cleanup_expired_sessions_task.apply().get()

        self.assertEqual(deleted, 1)
        self.assertFalse(Session.objects.filter(session_key=expired_key).exists())
        self.assertTrue(Session.objects.filter(session_key=live_key).exists())

    def test_cleanup_chunks_through_a_large_backlog(self):
        now = timezone.now()
        for _ in range(5):
            self._make_session(expire_date=now - timedelta(days=2))

        deleted = cleanup_expired_sessions_task.apply(kwargs={"chunk_size": 2}).get()

        self.assertEqual(deleted, 5)
        self.assertEqual(Session.objects.count(), 0)

    def test_cleanup_is_scheduled(self):
        from django.conf import settings

        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        self.assertIn("cleanup-expired-sessions-daily", schedule)
        self.assertEqual(
            schedule["cleanup-expired-sessions-daily"]["task"],
            "reports.tasks.cleanup_expired_sessions_task",
        )


class LandingPricingCacheTests(TestCase):
    """The landing page runs in full for every campaign visitor (it is
    deliberately no-store), so its pricing queries must not run per visit."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        SubscriptionPlan.objects.create(
            name="باقة الاختبار",
            price=650,
            days_duration=180,
            max_teachers=50,
        )

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def test_repeat_visits_do_not_requery_the_plans(self):
        from reports.views.auth import landing_pricing_context

        landing_pricing_context()

        with self.assertNumQueries(0):
            landing_pricing_context()

    def test_landing_page_itself_stops_querying_plans_after_the_first_hit(self):
        url = reverse("reports:landing")
        self.client.get(url)

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        plan_queries = [
            q for q in captured.captured_queries
            if "subscriptionplan" in q["sql"].lower()
        ]
        self.assertEqual(plan_queries, [])

    def test_editing_a_plan_publishes_immediately(self):
        from reports.views.auth import landing_pricing_context

        landing_pricing_context()

        SubscriptionPlan.objects.create(
            name="باقة جديدة",
            price=1200,
            days_duration=365,
            max_teachers=100,
        )

        names = {
            card["capacity_label"] for card in landing_pricing_context()["pricing_cards"]
        }
        self.assertIn("حتى 100 معلماً", names)

    def test_deleting_a_plan_publishes_immediately(self):
        from reports.views.auth import landing_pricing_context

        landing_pricing_context()
        SubscriptionPlan.objects.filter(name="باقة الاختبار").delete()

        self.assertEqual(landing_pricing_context()["pricing_cards"], [])

    @override_settings(LANDING_PRICING_CACHE_TTL_SECONDS=0)
    def test_cache_can_be_disabled(self):
        from reports.views.auth import landing_pricing_context

        landing_pricing_context()

        # Plans + the flexible-pricing catalogue: the two queries the cache saves
        # on every visit.
        with self.assertNumQueries(2):
            landing_pricing_context()
