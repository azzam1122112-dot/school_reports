"""One command that answers "is production actually ready?".

Everything here is verified against the *running* environment — the live
settings, the real database, the real Redis — not against the repository. It is
meant to be run inside the production web container:

    docker compose -f compose.hetzner.yaml exec web \\
        python manage.py production_preflight

Exit code 0 means every check passed. 1 means at least one FAIL. Warnings never
fail the run: they flag things worth a decision, not things that are broken.
"""
from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


class Check:
    OK = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class Command(BaseCommand):
    help = "Verify the production environment end to end before or after a release."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Treat warnings as failures too.",
        )

    # ── output helpers ──────────────────────────────────────────────
    def _record(self, section, level, title, detail=""):
        self.results.append((section, level, title, detail))

    def _emit(self):
        current_section = None
        for section, level, title, detail in self.results:
            if section != current_section:
                self.stdout.write("")
                self.stdout.write(self.style.MIGRATE_HEADING(f"-- {section} --"))
                current_section = section
            style = {
                Check.OK: self.style.SUCCESS,
                Check.WARN: self.style.WARNING,
                Check.FAIL: self.style.ERROR,
            }[level]
            self.stdout.write(f"  {style(level):<6} {title}")
            if detail:
                self.stdout.write(f"         {detail}")

    # ── checks ──────────────────────────────────────────────────────
    def _check_runtime(self):
        section = "Runtime & security"
        env = str(getattr(settings, "ENV", "")).lower()
        if env == "production":
            self._record(section, Check.OK, "ENV is production")
        else:
            self._record(section, Check.FAIL, f"ENV is '{env}', expected 'production'")

        if getattr(settings, "DEBUG", True):
            self._record(section, Check.FAIL, "DEBUG is on — never in production")
        else:
            self._record(section, Check.OK, "DEBUG is off")

        if getattr(settings, "PRODUCTION_STRICT_MODE", False):
            self._record(section, Check.OK, "PRODUCTION_STRICT_MODE is on")
        else:
            self._record(
                section,
                Check.WARN,
                "PRODUCTION_STRICT_MODE is off",
                "Strict mode is what refuses SQLite, LocMem and public media in production.",
            )

        secret = str(getattr(settings, "SECRET_KEY", "") or "")
        if len(secret) < 50 or len(set(secret)) < 5:
            self._record(
                section, Check.FAIL, "SECRET_KEY is weak", "Needs 50+ varied characters."
            )
        else:
            self._record(section, Check.OK, "SECRET_KEY looks strong")

        hosts = list(getattr(settings, "ALLOWED_HOSTS", []))
        if "*" in hosts or not hosts:
            self._record(section, Check.FAIL, f"ALLOWED_HOSTS is unsafe: {hosts}")
        else:
            self._record(section, Check.OK, f"ALLOWED_HOSTS = {hosts}")

        for name in ("SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"):
            if getattr(settings, name, False):
                self._record(section, Check.OK, f"{name} is on")
            else:
                self._record(section, Check.FAIL, f"{name} is off")

        hsts = int(getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0)
        if hsts >= 15768000:
            self._record(section, Check.OK, f"HSTS = {hsts}s")
        else:
            self._record(section, Check.WARN, f"HSTS is only {hsts}s")

        if getattr(settings, "CSP_ENABLED", False) and not getattr(settings, "CSP_REPORT_ONLY", True):
            self._record(section, Check.OK, "CSP is enforced")
        else:
            self._record(section, Check.WARN, "CSP is off or report-only")

    def _check_database(self):
        section = "Database"
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            vendor = connection.vendor
        except Exception as exc:
            self._record(section, Check.FAIL, "Database unreachable", str(exc)[:160])
            return

        if vendor == "postgresql":
            self._record(section, Check.OK, "PostgreSQL reachable")
        else:
            self._record(section, Check.FAIL, f"Database vendor is '{vendor}', expected postgresql")

        conn_max_age = connection.settings_dict.get("CONN_MAX_AGE", 0)
        if conn_max_age:
            self._record(
                section,
                Check.WARN,
                f"CONN_MAX_AGE = {conn_max_age}",
                "Under ASGI each request gets a new thread, so persistent connections "
                "are never reused and only delay the close. Use 0 unless behind PgBouncer.",
            )
        else:
            self._record(section, Check.OK, "CONN_MAX_AGE = 0 (correct for ASGI)")

        # Unapplied migrations mean the running code and the schema disagree.
        try:
            from django.db.migrations.executor import MigrationExecutor

            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if pending:
                names = ", ".join(f"{m.app_label}.{m.name}" for m, _ in pending[:5])
                self._record(
                    section, Check.FAIL, f"{len(pending)} unapplied migration(s)", names
                )
            else:
                self._record(section, Check.OK, "All migrations applied")
        except Exception as exc:
            self._record(section, Check.WARN, "Could not read migration state", str(exc)[:160])

        # The connection budget the load shedder was sized against.
        if vendor == "postgresql":
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW max_connections")
                    db_max = int(cursor.fetchone()[0])
                    cursor.execute("SELECT count(*) FROM pg_stat_activity")
                    in_use = int(cursor.fetchone()[0])
                ceiling = int(getattr(settings, "MAX_CONCURRENT_REQUESTS", 0) or 0)
                detail = f"max_connections={db_max}, in use now={in_use}, MAX_CONCURRENT_REQUESTS={ceiling}"
                if ceiling and ceiling >= db_max:
                    self._record(
                        section,
                        Check.FAIL,
                        "Concurrency ceiling exceeds the database budget",
                        detail,
                    )
                else:
                    self._record(section, Check.OK, "Connection budget is sane", detail)
            except Exception as exc:
                self._record(section, Check.WARN, "Could not read max_connections", str(exc)[:160])

    def _check_cache_and_broker(self):
        section = "Redis, cache & queues"
        backend = str(settings.CACHES.get("default", {}).get("BACKEND", ""))
        if "redis" in backend.lower():
            self._record(section, Check.OK, "Cache backend is Redis")
        else:
            self._record(section, Check.FAIL, f"Cache backend is {backend}")

        try:
            from django.core.cache import cache

            cache.set("_preflight", 1, timeout=10)
            if cache.get("_preflight") != 1:
                self._record(section, Check.FAIL, "Cache write/read failed")
            else:
                self._record(section, Check.OK, "Cache read/write works")
        except Exception as exc:
            self._record(section, Check.FAIL, "Cache unreachable", str(exc)[:160])

        try:
            from django_redis import get_redis_connection

            client = get_redis_connection("default")
            memory = client.info(section="memory")
            policy = str(client.config_get("maxmemory-policy").get("maxmemory-policy", ""))
            used = int(memory.get("used_memory") or 0)
            limit = int(memory.get("maxmemory") or 0)

            if policy == "noeviction":
                self._record(
                    section,
                    Check.FAIL,
                    "maxmemory-policy is noeviction",
                    "A full Redis will reject every write, taking the Celery broker down "
                    "with the cache. Use volatile-lru so only keys with a TTL are evicted.",
                )
            else:
                self._record(section, Check.OK, f"maxmemory-policy = {policy}")

            if limit:
                percent = round(used / limit * 100, 1)
                level = Check.FAIL if percent >= 90 else (Check.WARN if percent >= 75 else Check.OK)
                self._record(
                    section,
                    level,
                    f"Redis memory at {percent}%",
                    f"{round(used / 1048576, 1)}MB of {round(limit / 1048576, 1)}MB",
                )
            else:
                self._record(section, Check.WARN, "Redis has no maxmemory set")

            for queue in ("default", "notifications", "images", "periodic"):
                depth = int(client.llen(queue) or 0)
                level = Check.WARN if depth > 1000 else Check.OK
                self._record(section, level, f"Queue '{queue}' depth = {depth}")
        except Exception as exc:
            self._record(section, Check.WARN, "Redis introspection unavailable", str(exc)[:160])

        try:
            from config.celery import app as celery_app

            replies = celery_app.control.ping(timeout=3) or []
            if replies:
                workers = ", ".join(sorted(k for reply in replies for k in reply))
                self._record(section, Check.OK, f"{len(replies)} Celery worker(s) responding", workers)
            else:
                self._record(
                    section,
                    Check.FAIL,
                    "No Celery worker responded",
                    "Notifications, image processing and every periodic job are dead without one.",
                )
        except Exception as exc:
            self._record(section, Check.WARN, "Could not ping Celery", str(exc)[:160])

    def _check_scheduled_jobs(self):
        section = "Scheduled jobs"
        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
        required = {
            "cleanup-expired-sessions-daily": "session table would grow without bound",
            "reconcile-pending-gateway-payments": "a paid school could stay unactivated",
            "check-storage-thresholds-daily": "managers would hit the storage wall unwarned",
            "check-subscription-expiry-daily": "subscriptions would lapse silently",
            "monitor-infrastructure-capacity": "Redis pressure would go unnoticed",
        }
        for name, why in required.items():
            if name in schedule:
                self._record(section, Check.OK, name)
            else:
                self._record(section, Check.FAIL, f"{name} is not scheduled", f"Without it, {why}.")

        if not schedule:
            self._record(section, Check.FAIL, "No beat schedule at all")

    def _check_storage(self):
        section = "Media storage"
        backend = str(settings.STORAGES.get("default", {}).get("BACKEND", ""))
        if "R2" in backend or "s3" in backend.lower():
            self._record(section, Check.OK, f"Media backend = {backend.rsplit('.', 1)[-1]}")
        else:
            self._record(
                section,
                Check.FAIL,
                "Media is stored on the container filesystem",
                "Container storage is not durable; uploads are lost on redeploy.",
            )

        if getattr(settings, "MEDIA_PUBLIC_ACCESS_ENABLED", False):
            self._record(
                section,
                Check.FAIL,
                "MEDIA_PUBLIC_ACCESS_ENABLED is on",
                "School reports, receipts and evidence would be publicly reachable.",
            )
        else:
            self._record(section, Check.OK, "Media is private")

        if getattr(settings, "AWS_QUERYSTRING_AUTH", True):
            self._record(section, Check.OK, "Media URLs are signed and expiring")
        else:
            self._record(section, Check.FAIL, "Media URLs are unsigned")

        try:
            from django.core.files.storage import default_storage

            default_storage.exists("preflight-probe-does-not-exist")
            self._record(section, Check.OK, "Object storage reachable")
        except Exception as exc:
            self._record(section, Check.FAIL, "Object storage unreachable", str(exc)[:160])

    def _check_email(self):
        section = "Email"
        backend = str(getattr(settings, "EMAIL_BACKEND", ""))
        if "smtp" not in backend.lower():
            self._record(section, Check.FAIL, f"EMAIL_BACKEND = {backend}")
            return
        host = str(getattr(settings, "EMAIL_HOST", "") or "").lower()
        if not host or host in {"localhost", "127.0.0.1"}:
            self._record(section, Check.FAIL, f"EMAIL_HOST = '{host}'")
        else:
            self._record(section, Check.OK, f"SMTP host = {host}")
        if "@" in str(getattr(settings, "DEFAULT_FROM_EMAIL", "")):
            self._record(section, Check.OK, f"From address = {settings.DEFAULT_FROM_EMAIL}")
        else:
            self._record(section, Check.FAIL, "DEFAULT_FROM_EMAIL is not an address")

    def _check_payments(self):
        section = "Payments"
        if getattr(settings, "MOYASAR_ENABLED", False):
            env = str(getattr(settings, "MOYASAR_ENVIRONMENT", ""))
            key = str(getattr(settings, "MOYASAR_SECRET_KEY", "") or "")
            if env == "live" and key.startswith("sk_live_"):
                self._record(section, Check.OK, "Moyasar is live with a live key")
            else:
                self._record(
                    section,
                    Check.FAIL,
                    f"Moyasar enabled but environment='{env}'",
                    "Real customers would be charged against test credentials, or not at all.",
                )
        else:
            self._record(
                section, Check.WARN, "Moyasar is disabled", "No electronic payment is possible."
            )

        if getattr(settings, "PAYMENT_RECONCILIATION_ENABLED", False):
            self._record(section, Check.OK, "Payment reconciliation is on")
        else:
            self._record(
                section,
                Check.FAIL,
                "Payment reconciliation is off",
                "A dropped gateway callback would leave a paying school unactivated.",
            )

        Payment = apps.get_model("reports", "Payment")
        try:
            stuck = Payment.objects.filter(
                status=Payment.Status.PENDING,
                payment_method__in=[Payment.Method.MOYASAR, Payment.Method.TAMARA],
            ).count()
            level = Check.WARN if stuck else Check.OK
            self._record(section, level, f"{stuck} pending gateway payment(s)")
        except Exception as exc:
            self._record(section, Check.WARN, "Could not count pending payments", str(exc)[:160])

    def _check_pricing(self):
        section = "Pricing & storage model"
        SubscriptionPlan = apps.get_model("reports", "SubscriptionPlan")
        paid = list(SubscriptionPlan.objects.filter(is_active=True, price__gt=0))
        if not paid:
            self._record(section, Check.FAIL, "No active paid plan is published")
            return
        self._record(section, Check.OK, f"{len(paid)} active paid plan(s)")

        # The invariant that keeps interpolated prices honest.
        entitlements = {
            (
                plan.support_level,
                plan.onboarding_sessions,
                plan.included_archive_storage_gb,
            )
            for plan in paid
        }
        if len(entitlements) > 1:
            self._record(
                section,
                Check.FAIL,
                "Paid anchors carry different entitlements",
                "Prices between anchors are interpolated, so an entitlement step creates a "
                "capacity band where a school pays more and receives less.",
            )
        else:
            self._record(section, Check.OK, "All paid anchors share one entitlement set")

        try:
            from reports.flexible_pricing import build_flexible_pricing_catalog

            catalog = build_flexible_pricing_catalog(plans=paid)
            broken = []
            for group in catalog:
                quotes = group["quotes"]
                for earlier, later in zip(quotes, quotes[1:]):
                    if Decimal(later["price"]) <= Decimal(earlier["price"]):
                        broken.append(
                            f"{group['label']}: {later['capacity']} <= {earlier['capacity']}"
                        )
            if broken:
                self._record(section, Check.FAIL, "Price curve goes downhill", "; ".join(broken[:3]))
            else:
                self._record(section, Check.OK, f"Price curve rises across {len(catalog)} period(s)")
        except Exception as exc:
            self._record(section, Check.WARN, "Could not build the pricing catalog", str(exc)[:160])

        try:
            PlatformSettings = apps.get_model("reports", "PlatformSettings")
            per_teacher = int(getattr(PlatformSettings.get_solo(), "storage_mb_per_teacher", 0) or 0)
            if per_teacher > 0:
                self._record(section, Check.OK, f"Base storage = {per_teacher}MB per teacher seat")
            else:
                self._record(
                    section,
                    Check.WARN,
                    "storage_mb_per_teacher is 0",
                    "Schools fall back to the flat floor instead of capacity-based storage.",
                )
        except Exception as exc:
            self._record(section, Check.WARN, "Could not read platform settings", str(exc)[:160])

    def _check_business_identity(self):
        section = "Business disclosure"
        required = {
            "BUSINESS_LEGAL_NAME": "legal name",
            "BUSINESS_ADDRESS": "address",
            "BUSINESS_SUPPORT_EMAIL": "support email",
            "BUSINESS_SUPPORT_PHONE": "support phone",
        }
        missing = [label for name, label in required.items() if not str(getattr(settings, name, "") or "").strip()]
        registration = str(getattr(settings, "BUSINESS_COMMERCIAL_REGISTRATION", "") or "").strip()
        freelance = str(getattr(settings, "BUSINESS_FREELANCE_DOCUMENT_NUMBER", "") or "").strip()
        if not (registration or freelance):
            missing.append("commercial registration or freelance document")
        if missing:
            self._record(section, Check.FAIL, "Incomplete public disclosure", ", ".join(missing))
        else:
            self._record(section, Check.OK, "Public business identity is complete")

    def _check_observability(self):
        section = "Observability"
        if str(getattr(settings, "SENTRY_DSN", "") or "").strip():
            self._record(section, Check.OK, "Sentry is configured")
        else:
            self._record(section, Check.WARN, "SENTRY_DSN is not set", "Errors go to logs only.")

        if getattr(settings, "TELEGRAM_ALERTS_ENABLED", False):
            token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
            chat = str(getattr(settings, "TELEGRAM_ALERT_CHAT_ID", "") or "").strip()
            if token and chat:
                categories = sorted(getattr(settings, "TELEGRAM_ALERT_CATEGORIES", set()) or set())
                self._record(section, Check.OK, "Telegram alerts on", f"categories: {', '.join(categories)}")
            else:
                self._record(section, Check.FAIL, "Telegram alerts on but token/chat missing")
        else:
            self._record(
                section,
                Check.WARN,
                "Telegram alerts are off",
                "Rescued payments and capacity warnings reach nobody in real time.",
            )

    # ── entry point ─────────────────────────────────────────────────
    def handle(self, *args, **options):
        self.results: list[tuple[str, str, str, str]] = []

        self.stdout.write(self.style.MIGRATE_HEADING("=== Production preflight ==="))

        for check in (
            self._check_runtime,
            self._check_database,
            self._check_cache_and_broker,
            self._check_scheduled_jobs,
            self._check_storage,
            self._check_email,
            self._check_payments,
            self._check_pricing,
            self._check_business_identity,
            self._check_observability,
        ):
            try:
                check()
            except Exception as exc:  # a broken check must not hide the others
                self._record("Preflight", Check.FAIL, f"{check.__name__} crashed", str(exc)[:200])

        self._emit()

        failures = sum(1 for _, level, _, _ in self.results if level == Check.FAIL)
        warnings = sum(1 for _, level, _, _ in self.results if level == Check.WARN)
        passed = sum(1 for _, level, _, _ in self.results if level == Check.OK)

        self.stdout.write("")
        summary = f"{passed} passed, {warnings} warning(s), {failures} failure(s)"
        if failures:
            self.stdout.write(self.style.ERROR(f"NOT READY — {summary}"))
        elif warnings and options.get("strict"):
            self.stdout.write(self.style.ERROR(f"NOT READY (strict) — {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"READY — {summary}"))

        if failures or (warnings and options.get("strict")):
            raise SystemExit(1)
