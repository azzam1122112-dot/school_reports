from django.db import migrations, models


class Migration(migrations.Migration):
    """Platform switch for voice dictation, alongside the other AI toggles."""

    dependencies = [
        ("reports", "0113_drop_tamara_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformsettings",
            name="voice_report_enabled",
            field=models.BooleanField(
                default=True,
                db_index=True,
                help_text="إظهار مسجّل الصوت في صفحة إضافة تقرير والسماح بتفريغه نصًا.",
                verbose_name="إظهار كتابة التقرير بالصوت",
            ),
        ),
    ]
