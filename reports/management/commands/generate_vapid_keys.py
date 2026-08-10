from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand
from py_vapid import Vapid


def _base64url(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate a VAPID key pair for PWA Web Push environment variables."

    def handle(self, *args, **options):
        vapid = Vapid()
        vapid.generate_keys()
        private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        public_raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )

        self.stdout.write("WEB_PUSH_ENABLED=True")
        self.stdout.write(f"WEB_PUSH_VAPID_PRIVATE_KEY={_base64url(private_raw)}")
        self.stdout.write(f"WEB_PUSH_VAPID_PUBLIC_KEY={_base64url(public_raw)}")
        self.stdout.write("WEB_PUSH_SUBJECT=mailto:support@tawtheeq-ksa.com")
