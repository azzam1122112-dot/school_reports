from django.db import migrations, models


def purge_supervisor_data(apps, schema_editor):
    """Delete report-viewer memberships and delegated platform-admin accounts.

    The system owner (``is_superuser``) is never deleted, whatever the state of
    ``is_platform_admin`` on that row.

    What follows a deleted delegated account: authorship fields are ``SET_NULL``
    (payments, subscriptions, notifications, reports, audit-log actor), so that
    history survives without an author. Rows that only exist *because of* the
    account cascade away with it — the platform tickets it opened, its ticket
    notes, and its ``AuditLog`` rows.
    """
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Teacher = apps.get_model("reports", "Teacher")

    SchoolMembership.objects.filter(role_type="report_viewer").delete()
    Teacher.objects.filter(is_platform_admin=True, is_superuser=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0089_school_group_executive_director"),
    ]

    operations = [
        # Data purge first: the delegated accounts must go before the field and
        # the scope models that describe them are dropped. Irreversible on
        # purpose — the deleted rows cannot be reconstructed on a reverse run.
        migrations.RunPython(purge_supervisor_data, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="platformadminscope",
            name="admin",
        ),
        migrations.RemoveField(
            model_name="platformadminscope",
            name="allowed_schools",
        ),
        migrations.RemoveField(
            model_name="platformadminscope",
            name="role",
        ),
        migrations.DeleteModel(name="PlatformAdminScope"),
        migrations.DeleteModel(name="PlatformAdminRole"),
        migrations.RemoveField(
            model_name="teacher",
            name="is_platform_admin",
        ),
        migrations.AlterField(
            model_name="schoolmembership",
            name="role_type",
            field=models.CharField(
                "الدور داخل المدرسة",
                choices=[("teacher", "معلم"), ("manager", "مدير مدرسة")],
                default="teacher",
                max_length=16,
            ),
        ),
    ]
