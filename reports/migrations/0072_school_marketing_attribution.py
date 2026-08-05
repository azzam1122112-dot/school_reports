from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0071_customercomplaint"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="marketing_campaign",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="اسم الحملة"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_click_id",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="معرف نقرة الإعلان"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_content",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="محتوى الإعلان"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_medium",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="وسيط الحملة"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_referrer",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="نطاق الإحالة"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_source",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="مصدر التسجيل التسويقي"),
        ),
        migrations.AddField(
            model_name="school",
            name="marketing_term",
            field=models.CharField(blank=True, default="", max_length=200, verbose_name="الكلمة التسويقية"),
        ),
    ]
