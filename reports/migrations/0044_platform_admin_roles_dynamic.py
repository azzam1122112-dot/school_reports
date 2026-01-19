from django.db import migrations, models


def _seed_roles_and_migrate_scope_role(apps, schema_editor):
    PlatformAdminRole = apps.get_model("reports", "PlatformAdminRole")
    PlatformAdminScope = apps.get_model("reports", "PlatformAdminScope")

    # Create default roles corresponding to the historical fixed choices.
    defaults = [
        ("general", "مشرف عام", 0),
        ("education_manager", "مدير التعليم", 10),
        ("minister", "وزير التعليم", 20),
        ("resident", "مشرف مقيم", 30),
    ]

    slug_to_role_id = {}
    for slug, name, order in defaults:
        role_obj, _ = PlatformAdminRole.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "order": order, "is_active": True},
        )
        # Ensure name/order in case it already exists
        try:
            changed = False
            if (role_obj.name or "") != name:
                role_obj.name = name
                changed = True
            if getattr(role_obj, "order", 0) != order:
                role_obj.order = order
                changed = True
            if hasattr(role_obj, "is_active") and not bool(role_obj.is_active):
                role_obj.is_active = True
                changed = True
            if changed:
                role_obj.save()
        except Exception:
            pass

        slug_to_role_id[slug] = role_obj.pk

    # Migrate PlatformAdminScope.role (old CharField) into role_fk
    for scope in PlatformAdminScope.objects.all().iterator():
        old_value = (getattr(scope, "role", None) or "general").strip()
        if old_value not in slug_to_role_id:
            old_value = "general"
        setattr(scope, "role_fk_id", slug_to_role_id[old_value])
        scope.save(update_fields=["role_fk"])


def _reverse_noop(apps, schema_editor):
    # We don't restore deleted choice field on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0043_platformadminscope_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformAdminRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64, unique=True, verbose_name="اسم الدور")),
                ("slug", models.SlugField(max_length=64, unique=True, verbose_name="المعرّف (slug)")),
                ("is_active", models.BooleanField(default=True, verbose_name="نشط")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="ترتيب")),
            ],
            options={
                "verbose_name": "دور مشرف منصة",
                "verbose_name_plural": "أدوار مشرفي المنصة",
                "ordering": ("order", "id"),
            },
        ),
        migrations.AddField(
            model_name="platformadminscope",
            name="role_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="scopes",
                to="reports.platformadminrole",
                verbose_name="الدور",
            ),
        ),
        migrations.RunPython(_seed_roles_and_migrate_scope_role, _reverse_noop),
        migrations.RemoveField(
            model_name="platformadminscope",
            name="role",
        ),
        migrations.RenameField(
            model_name="platformadminscope",
            old_name="role_fk",
            new_name="role",
        ),
    ]
