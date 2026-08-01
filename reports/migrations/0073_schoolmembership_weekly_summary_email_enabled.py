from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0072_school_marketing_attribution"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolmembership",
            name="weekly_summary_email_enabled",
            field=models.BooleanField(
                default=True,
                help_text="خاص بمدير المدرسة: عند إيقافه لن تُرسل رسائل الملخص الأسبوعي لهذا المدير.",
                verbose_name="استقبال الملخص الأسبوعي على الإيميل",
            ),
        ),
    ]
