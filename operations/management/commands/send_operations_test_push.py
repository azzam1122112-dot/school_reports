from django.core.management.base import BaseCommand, CommandError

from operations.models import Incident, ManagedServer, MobileDevice
from operations.push import send_incident_push


class Command(BaseCommand):
    help = "Send a real FCM test notification to registered Operations devices."

    def handle(self, *args, **options):
        expected = MobileDevice.objects.filter(
            is_active=True,
            alerts_enabled=True,
        ).exclude(fcm_token="").count()
        if expected == 0:
            raise CommandError("No active Operations device has an FCM token")
        server = ManagedServer.objects.filter(is_active=True).order_by("id").first()
        incident = Incident.objects.create(
            server=server,
            dedupe_key="operations:test-push",
            title="اختبار تنبيهات مركز العمليات",
            message="وصل التنبيه بنجاح والهاتف خارج التطبيق. لا يلزم إجراء.",
            severity=Incident.Severity.INFO,
        )
        try:
            result = send_incident_push(incident)
        finally:
            incident.delete()
        if result["sent"] != expected or result["failed"] or result["disabled"]:
            raise CommandError(
                "FCM delivery failed: "
                f"expected={expected} sent={result['sent']} "
                f"failed={result['failed']} disabled={result['disabled']}"
            )
        self.stdout.write(self.style.SUCCESS(f"FCM accepted the notification for {result['sent']} device(s)"))
