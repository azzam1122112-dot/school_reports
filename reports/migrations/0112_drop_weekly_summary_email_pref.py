from django.db import migrations


class Migration(migrations.Migration):
    """Drop the weekly-summary email preference.

    The weekly manager summary is now an in-app notification only, so the
    per-manager opt-in has nothing left to switch off.
    """

    dependencies = [
        ("reports", "0111_web_push_notifications"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="schoolmembership",
            name="weekly_summary_email_enabled",
        ),
    ]
