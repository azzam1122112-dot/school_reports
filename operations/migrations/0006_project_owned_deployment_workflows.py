from django.db import migrations


def use_project_owned_workflows(apps, schema_editor):
    ManagedProject = apps.get_model("operations", "ManagedProject")
    configurations = {
        "xmansx": "azzam1122112-dot/Tanal-Barbershop-Interface",
        "school-display": "azzam1122112-dot/school_display",
    }
    for slug, repository in configurations.items():
        ManagedProject.objects.filter(slug=slug).update(
            deploy_repository=repository,
            deploy_workflow="deploy.yml",
            deployment_enabled=True,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0005_managed_deployment_control"),
    ]

    operations = [
        migrations.RunPython(use_project_owned_workflows, migrations.RunPython.noop),
    ]
