from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0080_school_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="goal",
            field=models.TextField(blank=True, default="", verbose_name="الهدف"),
        ),
        migrations.AddField(
            model_name="report",
            name="implementation_method",
            field=models.TextField(blank=True, default="", verbose_name="آلية التنفيذ"),
        ),
        migrations.AddField(
            model_name="report",
            name="recommendations",
            field=models.TextField(blank=True, default="", verbose_name="التوصيات"),
        ),
        migrations.AddField(
            model_name="report",
            name="results",
            field=models.TextField(blank=True, default="", verbose_name="النتائج"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_beneficiaries",
            field=models.BooleanField(default=True, verbose_name="إظهار عدد المستفيدين"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_details",
            field=models.BooleanField(default=True, verbose_name="إظهار تفاصيل التقرير"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_goal",
            field=models.BooleanField(default=False, verbose_name="إظهار الهدف"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_implementation",
            field=models.BooleanField(default=False, verbose_name="إظهار آلية التنفيذ"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_recommendations",
            field=models.BooleanField(default=False, verbose_name="إظهار التوصيات"),
        ),
        migrations.AddField(
            model_name="report",
            name="show_results",
            field=models.BooleanField(default=False, verbose_name="إظهار النتائج"),
        ),
    ]
