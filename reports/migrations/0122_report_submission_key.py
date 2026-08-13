import uuid

from django.db import migrations, models


def populate_submission_keys(apps, schema_editor):
    Report = apps.get_model("reports", "Report")
    for report in Report.objects.filter(submission_key__isnull=True).iterator(chunk_size=500):
        Report.objects.filter(pk=report.pk).update(submission_key=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("reports", "0121_report_evidence_and_structured_minutes")]

    operations = [
        migrations.AddField(
            model_name="report",
            name="submission_key",
            field=models.UUIDField(editable=False, null=True, verbose_name="معرّف الإرسال الآمن"),
        ),
        migrations.RunPython(populate_submission_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="report",
            name="submission_key",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="معرّف الإرسال الآمن"),
        ),
    ]
