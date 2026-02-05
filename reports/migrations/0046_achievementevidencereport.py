from django.db import migrations, models
import django.db.models.deletion

import reports.models
import reports.validators


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0045_schoolmembership_job_title"),
    ]

    operations = [
        migrations.CreateModel(
            name="AchievementEvidenceReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("frozen_at", models.DateTimeField(blank=True, null=True, verbose_name="تاريخ التجميد")),
                ("frozen_data", models.JSONField(blank=True, default=dict, verbose_name="بيانات التقرير (Snapshot)")),
                (
                    "archived_image1",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to=reports.models._achievement_report_evidence_upload_to,
                        validators=[reports.validators.validate_image_file],
                        verbose_name="صورة 1 (مؤرشفة)",
                    ),
                ),
                (
                    "archived_image2",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to=reports.models._achievement_report_evidence_upload_to,
                        validators=[reports.validators.validate_image_file],
                        verbose_name="صورة 2 (مؤرشفة)",
                    ),
                ),
                (
                    "archived_image3",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to=reports.models._achievement_report_evidence_upload_to,
                        validators=[reports.validators.validate_image_file],
                        verbose_name="صورة 3 (مؤرشفة)",
                    ),
                ),
                (
                    "archived_image4",
                    models.ImageField(
                        blank=True,
                        null=True,
                        upload_to=reports.models._achievement_report_evidence_upload_to,
                        validators=[reports.validators.validate_image_file],
                        verbose_name="صورة 4 (مؤرشفة)",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="achievement_evidences",
                        to="reports.report",
                        verbose_name="التقرير",
                    ),
                ),
                (
                    "section",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evidence_reports",
                        to="reports.achievementsection",
                        verbose_name="المحور",
                    ),
                ),
            ],
            options={
                "verbose_name": "تقرير شاهد",
                "verbose_name_plural": "تقارير الشواهد",
                "ordering": ["id"],
            },
        ),
        migrations.AddConstraint(
            model_name="achievementevidencereport",
            constraint=models.UniqueConstraint(
                fields=("section", "report"),
                name="uniq_achievement_section_report_evidence",
            ),
        ),
    ]
