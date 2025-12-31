from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from reports.models import School


class Command(BaseCommand):
    help = (
        "Set a default logo for any school that has no logo (logo_file) and no logo URL. "
        "The default logo is uploaded once to the configured media storage and reused."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default=str(Path(settings.BASE_DIR) / "static" / "img" / "UntiTtled-1.png"),
            help="Path to the source PNG under the project (default: static/img/UntiTtled-1.png)",
        )
        parser.add_argument(
            "--target-name",
            default="schools/logos/default.png",
            help="Target object name in media storage (default: schools/logos/default.png)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many schools would be updated without changing anything.",
        )

    def handle(self, *args, **options):
        source = Path(options["source"]).expanduser().resolve()
        target_name: str = options["target_name"]
        dry_run: bool = bool(options["dry_run"])

        missing_q = (
            (Q(logo_file__isnull=True) | Q(logo_file=""))
            & (Q(logo_url__isnull=True) | Q(logo_url=""))
        )

        missing_count = School.objects.filter(missing_q).count()
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN: would update {missing_count} school(s)."))
            return

        if missing_count == 0:
            self.stdout.write(self.style.SUCCESS("No schools missing a logo. Nothing to do."))
            return

        if not source.exists():
            raise FileNotFoundError(f"Default logo source not found: {source}")

        data = source.read_bytes()

        saved_name = target_name
        try:
            exists = default_storage.exists(target_name)
        except Exception:
            exists = False

        if not exists:
            saved_name = default_storage.save(target_name, ContentFile(data))

        updated = School.objects.filter(missing_q).update(
            logo_file=saved_name,
            updated_at=timezone.now(),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Uploaded default logo as '{saved_name}' and updated {updated} school(s)."
            )
        )
