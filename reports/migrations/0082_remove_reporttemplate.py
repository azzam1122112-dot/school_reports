from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0081_report_configurable_sections"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ReportTemplate",
        ),
    ]
