from django.db import migrations, models
import django.db.models.deletion
import reports.models
import reports.validators


def migrate_legacy_report_images(apps, schema_editor):
    Report = apps.get_model("reports", "Report")
    ReportEvidence = apps.get_model("reports", "ReportEvidence")
    batch = []
    for report in Report.objects.all().iterator(chunk_size=500):
        for order in range(1, 5):
            field = getattr(report, f"image{order}", None)
            if not field:
                continue
            try:
                size = int(field.size or 0)
            except Exception:
                size = 0
            batch.append(
                ReportEvidence(
                    report_id=report.pk,
                    image=str(field),
                    order=order,
                    description=f"مرفق توثيقي ({order})",
                    show_in_print=True,
                    storage_bytes=size,
                )
            )
        if len(batch) >= 1000:
            ReportEvidence.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        ReportEvidence.objects.bulk_create(batch, ignore_conflicts=True)
    # تظل الأعمدة القديمة في المخطط كطبقة توافق برمجية مؤقتة، لكن المرجع
    # الفعلي ينتقل إلى ReportEvidence كي لا تُحسب الصورة نفسها مرتين.
    Report.objects.all().update(
        image1="",
        image2="",
        image3="",
        image4="",
        storage_bytes=0,
    )


def restore_legacy_report_images(apps, schema_editor):
    Report = apps.get_model("reports", "Report")
    ReportEvidence = apps.get_model("reports", "ReportEvidence")
    for report in Report.objects.all().iterator(chunk_size=500):
        updates = {}
        total = 0
        evidences = ReportEvidence.objects.filter(report_id=report.pk).order_by("order", "id")[:4]
        for slot, evidence in enumerate(evidences, start=1):
            updates[f"image{slot}"] = str(evidence.image)
            total += int(evidence.storage_bytes or 0)
        if updates:
            updates["storage_bytes"] = total
            Report.objects.filter(pk=report.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("reports", "0120_platform_email_center")]

    operations = [
        migrations.AddField(
            model_name="report",
            name="evidence_page_mode",
            field=models.CharField(
                choices=[
                    ("auto", "تلقائي"),
                    ("inline", "ضمن التقرير"),
                    ("separate", "صفحة شواهد مستقلة"),
                ],
                default="auto",
                help_text="يختار الوضع التلقائي صفحة مستقلة عند كثرة الشواهد أو كبرها.",
                max_length=12,
                verbose_name="موضع صفحة الشواهد",
            ),
        ),
        migrations.CreateModel(
            name="ReportEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "image",
                    models.ImageField(
                        upload_to=reports.models._report_evidence_upload_to,
                        validators=[reports.validators.validate_image_file],
                        verbose_name="الصورة",
                    ),
                ),
                ("order", models.PositiveSmallIntegerField(db_index=True, default=1, verbose_name="الترتيب")),
                ("description", models.CharField(blank=True, default="", help_text="مثال: صورة من تنفيذ النشاط أو نموذج من أعمال الطلاب.", max_length=220, verbose_name="وصف الشاهد")),
                ("display_size", models.CharField(choices=[("auto", "تلقائي"), ("large", "كبير"), ("medium", "متوسط"), ("small", "صغير")], default="auto", max_length=10, verbose_name="حجم العرض")),
                ("fit_mode", models.CharField(choices=[("contain", "احتواء الصورة كاملة"), ("cover", "ملء الإطار")], default="contain", max_length=10, verbose_name="طريقة الملاءمة")),
                ("show_in_print", models.BooleanField(default=True, verbose_name="إظهار في الطباعة")),
                ("width_px", models.PositiveIntegerField(blank=True, editable=False, null=True, verbose_name="العرض بالبكسل")),
                ("height_px", models.PositiveIntegerField(blank=True, editable=False, null=True, verbose_name="الارتفاع بالبكسل")),
                ("storage_bytes", models.PositiveBigIntegerField(default=0, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("report", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="evidences", to="reports.report", verbose_name="التقرير")),
            ],
            options={
                "verbose_name": "شاهد تقرير",
                "verbose_name_plural": "شواهد التقارير",
                "ordering": ["order", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="reportevidence",
            index=models.Index(fields=["report", "show_in_print", "order"], name="reports_rep_report__9f5744_idx"),
        ),
        migrations.AddConstraint(
            model_name="reportevidence",
            constraint=models.UniqueConstraint(fields=("report", "order"), name="uniq_report_evidence_order"),
        ),
        migrations.RunPython(migrate_legacy_report_images, restore_legacy_report_images),
        migrations.AddField(
            model_name="meetingminutes",
            name="format_mode",
            field=models.CharField(choices=[("freeform", "نص موحد"), ("structured", "محضر منظم")], default="freeform", max_length=12, verbose_name="صيغة المحضر"),
        ),
        migrations.AddField(model_name="meetingminutes", name="proceedings", field=models.TextField(blank=True, default="", verbose_name="مجريات الاجتماع")),
        migrations.AddField(model_name="meetingminutes", name="discussions", field=models.TextField(blank=True, default="", verbose_name="أبرز النقاشات")),
        migrations.AddField(model_name="meetingminutes", name="decisions_summary", field=models.TextField(blank=True, default="", verbose_name="ملخص القرارات")),
        migrations.AddField(model_name="meetingminutes", name="recommendations", field=models.TextField(blank=True, default="", verbose_name="التوصيات")),
        migrations.AddField(model_name="meetingminutes", name="assignments_summary", field=models.TextField(blank=True, default="", verbose_name="التكليفات")),
    ]
