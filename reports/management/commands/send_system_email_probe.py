from __future__ import annotations

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from reports.email_branding import render_branded_email
from reports.email_identity import format_system_from_email


class Command(BaseCommand):
    help = (
        "Send one real system-email probe through the configured production "
        "backend. This verifies provider acceptance, not final inbox placement."
    )

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Inbox that should receive the probe.")

    def handle(self, *args, **options):
        recipient = str(options["recipient"] or "").strip().lower()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError("Recipient is not a valid email address.") from exc

        backend = str(getattr(settings, "EMAIL_BACKEND", "") or "").strip()
        if backend not in {
            "reports.email_backends.ResendEmailBackend",
            "django.core.mail.backends.smtp.EmailBackend",
        }:
            raise CommandError(
                "A real Resend or SMTP backend is required; refusing a console/locmem probe."
            )

        subject = "اختبار جاهزية البريد | منصة توثيق"
        plain = (
            "هذه رسالة تحقق تشغيلية من منصة توثيق.\n"
            "استلامها يؤكد أن مزود البريد قبل رسالة النظام الفعلية."
        )
        html = render_branded_email(
            "message.html",
            email_title="اختبار جاهزية البريد",
            email_eyebrow="فحص تشغيلي",
            email_preheader="رسالة تحقق من قناة بريد النظام.",
            email_tone="success",
            email_intro=(
                "هذه رسالة تحقق تشغيلية من منصة توثيق. استلامها يؤكد أن "
                "قناة بريد النظام تعمل عبر مزود البريد الفعلي."
            ),
            notice_title="لا يلزم اتخاذ أي إجراء",
            notice_text="أُرسلت هذه الرسالة بطلب من مشغّل المنصة للتحقق من الجاهزية.",
        )
        message = mail.EmailMultiAlternatives(
            subject=subject,
            body=plain,
            from_email=format_system_from_email(),
            to=[recipient],
        )
        message.attach_alternative(html, "text/html")

        try:
            sent = message.send(fail_silently=False)
        except Exception as exc:
            raise CommandError(f"System email probe was rejected: {exc}") from exc
        if sent != 1:
            raise CommandError(f"System email probe returned an unexpected sent count: {sent}")

        self.stdout.write(
            self.style.SUCCESS(
                "System email probe accepted by the configured provider. Check the recipient inbox."
            )
        )
