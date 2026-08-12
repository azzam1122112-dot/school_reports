from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import reports.model_parts.reports


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0118_sharelink_access_count"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="trashed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="نُقل إلى سلة المحذوفات في",
            ),
        ),
        migrations.AddField(
            model_name="report",
            name="trashed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="trashed_reports",
                to=settings.AUTH_USER_MODEL,
                verbose_name="نُقل إلى السلة بواسطة",
            ),
        ),
        migrations.AlterModelOptions(
            name="report",
            options={
                "base_manager_name": "all_objects",
                "default_manager_name": "objects",
                "ordering": ["-created_at"],
                "verbose_name": "تقرير",
                "verbose_name_plural": "التقارير",
            },
        ),
        migrations.AlterModelManagers(
            name="report",
            managers=[
                ("objects", reports.model_parts.reports.ActiveReportManager()),
                ("all_objects", models.Manager()),
            ],
        ),
    ]
