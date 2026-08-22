import re

from django.core.management.base import BaseCommand, CommandError

from operations.models import ManagedProject


class Command(BaseCommand):
    help = "Record the exact commit currently serving a managed project."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("sha")
        parser.add_argument("--image", default="")

    def handle(self, *args, **options):
        slug = str(options["slug"] or "").strip()
        sha = str(options["sha"] or "").strip().lower()
        image = str(options["image"] or "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise CommandError("sha must be a full 40-character Git commit")
        project = ManagedProject.objects.filter(slug=slug, is_active=True).first()
        if project is None:
            raise CommandError(f"active managed project not found: {slug}")
        values = {"deployed_sha": sha}
        if image:
            values["deployed_image"] = image[:300]
        ManagedProject.objects.filter(pk=project.pk).update(**values)
        self.stdout.write(self.style.SUCCESS(f"Stamped {slug} at {sha[:12]}"))
