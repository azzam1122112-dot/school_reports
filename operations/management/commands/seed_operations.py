from django.core.management.base import BaseCommand

from operations.models import ManagedProject, ManagedServer, ManagedService


class Command(BaseCommand):
    help = "Create the production server/project inventory used by the operations mobile app."

    def handle(self, *args, **options):
        server, _ = ManagedServer.objects.update_or_create(
            slug="school-reports-prod",
            defaults={
                "name": "school-reports-prod",
                "provider": "hetzner",
                "provider_server_id": "155662703",
                "public_ip": "178.104.163.3",
                "server_type": "CPX32",
                "is_active": True,
            },
        )
        projects = (
            ("tawtheeq", "منصة توثيق", "https://tawtheeq-ksa.com", "/healthz/"),
            ("xmansx", "منصة TANAL", "https://xmansx.com", "/api/health/readiness"),
            ("school-display", "لوحة العرض المدرسية", "https://school-display.com", "/healthz/"),
        )
        for order, (slug, name, url, path) in enumerate(projects, start=1):
            project, _ = ManagedProject.objects.update_or_create(
                slug=slug,
                defaults={"server": server, "name": name, "base_url": url, "health_path": path, "sort_order": order, "is_active": True},
            )
            for service_key, service_name, kind, restart_allowed in (
                ("web", "تطبيق الويب", ManagedService.Kind.WEB, True),
                ("database", "قاعدة البيانات", ManagedService.Kind.DATABASE, False),
                ("cache", "Redis", ManagedService.Kind.CACHE, True),
            ):
                ManagedService.objects.update_or_create(
                    project=project,
                    service_key=service_key,
                    defaults={"name": service_name, "kind": kind, "restart_allowed": restart_allowed, "is_active": True},
                )
        self.stdout.write(self.style.SUCCESS("Operations inventory is ready."))
