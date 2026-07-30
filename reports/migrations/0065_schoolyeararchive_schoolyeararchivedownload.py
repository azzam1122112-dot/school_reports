import django.conf
import django.db.models.deletion
from django.db import migrations, models

import reports.model_parts.billing


def mark_existing_approved_payment_effects(apps, schema_editor):
    """Existing approved rows already had their business effect applied before this marker existed."""
    Payment = apps.get_model("reports", "Payment")
    Payment.objects.filter(
        status="approved",
        effects_applied_at__isnull=True,
    ).update(effects_applied_at=models.F("updated_at"))


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
        ("reports", "0064_achievementevidenceimage_storage_bytes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="effects_applied_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text="يمنع تطبيق التفعيل أو زيادة المساحة أكثر من مرة لنفس عملية الدفع.",
                null=True,
                verbose_name="وقت تطبيق أثر الدفع",
            ),
        ),
        migrations.RunPython(
            mark_existing_approved_payment_effects,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name="SchoolYearArchive",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("academic_year", models.CharField(db_index=True, max_length=32, verbose_name="السنة الدراسية")),
                ("version", models.PositiveIntegerField(default=1, verbose_name="رقم النسخة")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ready", "مكتمل"),
                            ("partial", "مكتمل مع ملاحظات"),
                            ("failed", "فشل الإنشاء"),
                        ],
                        db_index=True,
                        default="ready",
                        max_length=16,
                        verbose_name="حالة النسخة",
                    ),
                ),
                (
                    "archive_file",
                    models.FileField(
                        blank=True,
                        max_length=500,
                        upload_to=reports.model_parts.billing.school_year_archive_upload_to,
                        verbose_name="ملف الأرشيف",
                    ),
                ),
                ("storage_bytes", models.PositiveBigIntegerField(default=0, verbose_name="حجم التخزين")),
                ("archive_sha256", models.CharField(blank=True, default="", max_length=64, verbose_name="بصمة ملف ZIP")),
                ("file_count", models.PositiveIntegerField(default=0, verbose_name="عدد الملفات")),
                ("missing_file_count", models.PositiveIntegerField(default=0, verbose_name="ملفات مفقودة")),
                ("failed_pdf_count", models.PositiveIntegerField(default=0, verbose_name="ملفات PDF متعذرة")),
                ("report_count", models.PositiveIntegerField(default=0, verbose_name="عدد التقارير")),
                ("achievement_count", models.PositiveIntegerField(default=0, verbose_name="عدد ملفات الإنجاز")),
                ("notes", models.TextField(blank=True, default="", verbose_name="تقرير الإنشاء")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاريخ إنشاء النسخة")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_school_year_archives",
                        to=django.conf.settings.AUTH_USER_MODEL,
                        verbose_name="أنشأها",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="year_archives",
                        to="reports.school",
                        verbose_name="المدرسة",
                    ),
                ),
            ],
            options={
                "verbose_name": "نسخة أرشيف سنة",
                "verbose_name_plural": "نسخ أرشيف السنوات",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.CreateModel(
            name="SchoolYearArchiveDownload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("downloaded_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="وقت التنزيل")),
                (
                    "archive",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="downloads",
                        to="reports.schoolyeararchive",
                        verbose_name="نسخة الأرشيف",
                    ),
                ),
                (
                    "downloaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="school_archive_downloads",
                        to=django.conf.settings.AUTH_USER_MODEL,
                        verbose_name="نزّلها",
                    ),
                ),
            ],
            options={
                "verbose_name": "تنزيل نسخة أرشيف",
                "verbose_name_plural": "تنزيلات نسخ الأرشيف",
                "ordering": ("-downloaded_at", "-id"),
            },
        ),
        migrations.AddConstraint(
            model_name="schoolyeararchive",
            constraint=models.UniqueConstraint(
                fields=("school", "academic_year", "version"),
                name="uniq_school_year_archive_version",
            ),
        ),
        migrations.AddIndex(
            model_name="schoolyeararchive",
            index=models.Index(
                fields=["school", "academic_year", "-created_at"],
                name="reports_sya_school_year_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="schoolyeararchive",
            index=models.Index(fields=["school", "status"], name="reports_sya_school_status_idx"),
        ),
        migrations.AddIndex(
            model_name="schoolyeararchivedownload",
            index=models.Index(fields=["archive", "-downloaded_at"], name="reports_syad_archive_date_idx"),
        ),
    ]
