from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0082_remove_reporttemplate"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="included_archive_storage_gb",
            field=models.PositiveIntegerField(
                default=0,
                help_text="تُفعّل تلقائياً عند اعتماد دفع الباقة. القيمة 0 تعني أن الأرشيف غير مشمول.",
                verbose_name="مساحة الأرشيف المشمولة (GB)",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="onboarding_sessions",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="جلسات الإعداد المشمولة"),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="support_level",
            field=models.CharField(
                choices=[("standard", "دعم اعتيادي"), ("priority", "دعم بأولوية")],
                default="standard",
                max_length=16,
                verbose_name="مستوى الدعم",
            ),
        ),
    ]
