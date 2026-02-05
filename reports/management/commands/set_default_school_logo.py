from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "DEPRECATED: School logos (logo_file/logo_url) were removed from the system."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "School logos (logo_file/logo_url) were removed from the system; this command is now a no-op."
            )
        )
