from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0076_platformsettings_ai_feature_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolyeararchive",
            name="leadership_count",
            field=models.PositiveIntegerField(
                default=0,
                verbose_name="عدد ملفات الأداء القيادي",
            ),
        ),
    ]
