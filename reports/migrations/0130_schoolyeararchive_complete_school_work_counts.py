from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0129_lab_kinds_independent_from_departments"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolyeararchive",
            name="assignment_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد التكليفات"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="plan_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد الخطط"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="initiative_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد المبادرات"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="lab_asset_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد أصول المختبر"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="lab_handover_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد حركات العهدة"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="lab_experiment_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد تجارب المختبر"),
        ),
    ]
