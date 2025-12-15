from django.db import migrations, models


def assign_existing_to_first_school(apps, schema_editor):
    School = apps.get_model("reports", "School")
    Department = apps.get_model("reports", "Department")
    ReportType = apps.get_model("reports", "ReportType")

    school = (
        School.objects.filter(is_active=True).order_by("id").first()
        or School.objects.order_by("id").first()
    )
    if not school:
        return

    Department.objects.filter(school__isnull=True).update(school=school)
    ReportType.objects.filter(school__isnull=True).update(school=school)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0012_school_city_school_gender_school_phone_school_stage"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="school",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="departments",
                verbose_name="المدرسة",
                help_text="يظهر هذا القسم فقط داخل المدرسة المحددة.",
                to="reports.school",
                null=True,
                blank=True,
            ),
        ),
        migrations.AddField(
            model_name="reporttype",
            name="school",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="report_types",
                verbose_name="المدرسة",
                help_text="يظهر هذا النوع فقط في المدرسة المحددة.",
                to="reports.school",
                null=True,
                blank=True,
            ),
        ),
        migrations.RunPython(assign_existing_to_first_school, migrations.RunPython.noop),
    ]
