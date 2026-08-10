from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0109_teacher_passkey_prompt_opt_out"),
    ]

    operations = [
        migrations.AlterField(
            model_name="labexperiment",
            name="title",
            field=models.CharField(
                blank=True,
                default="",
                max_length=200,
                verbose_name="عنوان التجربة",
            ),
        ),
        migrations.AlterField(
            model_name="labexperiment",
            name="experiment_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="تاريخ التنفيذ",
            ),
        ),
    ]
