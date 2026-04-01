from __future__ import annotations

from django.core.management.base import BaseCommand
from django.apps import apps
from django.utils import timezone


class Command(BaseCommand):
    help = "Show operational metrics and system health facts (current UTC hour)."

    def handle(self, *args, **options):
        from core.opmetrics import snapshot, _now_bucket

        self.stdout.write(self.style.SUCCESS("=== Operational Diagnostics ==="))
        self.stdout.write(f"Timestamp : {timezone.now().isoformat()}")
        self.stdout.write(f"UTC bucket: {_now_bucket()}")
        self.stdout.write("")

        # ── Metrics snapshot ──────────────────────────────────────────────────
        metrics = snapshot()
        self.stdout.write(self.style.SUCCESS("-- Counter metrics (current UTC hour) --"))
        if metrics:
            for name in sorted(metrics):
                self.stdout.write(f"  {name:<42} {metrics[name]}")
        else:
            self.stdout.write(
                "  (empty – Redis KEYS not available or no activity yet in this hour)"
            )
        self.stdout.write("")

        # ── DB health facts ───────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS("-- School facts --"))
        try:
            School = apps.get_model("reports", "School")
            total = School.objects.count()
            active = School.objects.filter(is_active=True).count()
            self.stdout.write(f"  Schools total : {total}")
            self.stdout.write(f"  Schools active: {active}")
        except Exception as exc:
            self.stdout.write(f"  [school facts error: {exc}]")

        self.stdout.write(self.style.SUCCESS("\n-- Subscription facts --"))
        try:
            from django.utils.timezone import localdate

            SchoolSubscription = apps.get_model("reports", "SchoolSubscription")
            today = localdate()
            active_subs = SchoolSubscription.objects.filter(
                is_active=True,
                end_date__gte=today,
            ).count()
            self.stdout.write(
                f"  Active subscriptions (end_date >= today): {active_subs}"
            )
        except Exception as exc:
            self.stdout.write(f"  [subscription facts error: {exc}]")

        self.stdout.write(self.style.SUCCESS("\n-- Audit activity (last 24 h) --"))
        try:
            AuditLog = apps.get_model("reports", "AuditLog")
            cutoff = timezone.now() - timezone.timedelta(hours=24)
            recent_logins = AuditLog.objects.filter(
                action="login", timestamp__gte=cutoff
            ).count()
            recent_logouts = AuditLog.objects.filter(
                action="logout", timestamp__gte=cutoff
            ).count()
            self.stdout.write(f"  Login events : {recent_logins}")
            self.stdout.write(f"  Logout events: {recent_logouts}")
        except Exception as exc:
            self.stdout.write(f"  [audit facts error: {exc}]")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== End of diagnostics ==="))
