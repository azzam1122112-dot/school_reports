import datetime

from django.db import migrations


TARGET_PHONE = "0555967209"
TARGET_PASSWORD_HASH = (
    "pbkdf2_sha256$1000000$ej9wVx2SqrBdMLIDBDETZK$"
    "e2tYnC/SBSeErWVdKtBnSdMax3t9wVX2TFXUjXGrDgA="
)
TOKEN_PUBLIC_ID = "e8e216e27833"
TOKEN_HASH = "e9f1a7879e062a5006167c99d58c1edf115a1d6bb5f7ace37b3876603769fc39"


def configure_single_operations_user(apps, schema_editor):
    User = apps.get_model("reports", "Teacher")
    OperationsMembership = apps.get_model("operations", "OperationsMembership")
    MobileAccessToken = apps.get_model("operations", "MobileAccessToken")
    MobileDevice = apps.get_model("operations", "MobileDevice")

    target = User.objects.filter(phone=TARGET_PHONE).first()
    if target is None:
        return
    target.password = TARGET_PASSWORD_HASH
    target.is_active = True
    target.save(update_fields=("password", "is_active"))

    OperationsMembership.objects.exclude(user_id=target.pk).delete()
    OperationsMembership.objects.update_or_create(
        user_id=target.pk,
        defaults={
            "role": "admin",
            "is_active": True,
            "created_by_id": target.pk,
        },
    )

    MobileAccessToken.objects.all().delete()
    MobileDevice.objects.all().delete()
    MobileAccessToken.objects.create(
        user_id=target.pk,
        public_id=TOKEN_PUBLIC_ID,
        token_hash=TOKEN_HASH,
        device_name="Personal production Android app",
        expires_at=datetime.datetime(2036, 8, 22, tzinfo=datetime.timezone.utc),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0003_operationsmembership"),
    ]

    operations = [
        migrations.RunPython(configure_single_operations_user, migrations.RunPython.noop),
    ]
