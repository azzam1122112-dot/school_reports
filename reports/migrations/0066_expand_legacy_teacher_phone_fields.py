from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0065_schoolyeararchive_schoolyeararchivedownload"),
    ]

    operations = [
        migrations.AlterField(
            model_name="teacher",
            name="phone",
            field=models.CharField(max_length=64, unique=True, verbose_name="رقم الجوال"),
        ),
        migrations.AlterField(
            model_name="teacherachievementfile",
            name="teacher_phone",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="رقم الجوال"),
        ),
    ]
