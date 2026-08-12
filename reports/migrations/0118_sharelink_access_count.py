from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0117_totp_devices")]

    operations = [
        migrations.AddField(
            model_name="sharelink",
            name="access_count",
            field=models.PositiveBigIntegerField(default=0, verbose_name="عدد مرات الفتح"),
        ),
    ]
