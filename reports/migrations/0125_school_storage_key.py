from django.db import migrations, models


def populate_storage_keys(apps, schema_editor):
    School = apps.get_model("reports", "School")
    for school in School.objects.all().only("pk", "code").iterator(chunk_size=500):
        key = (school.code or f"school-{school.pk}").strip().lower()
        School.objects.filter(pk=school.pk).update(storage_key=key)


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0124_restore_tamara_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="storage_key",
            field=models.SlugField(
                default="",
                editable=False,
                max_length=96,
                verbose_name="مفتاح مجلد التخزين",
                help_text="معرّف ثابت لمجلد المدرسة في التخزين، ولا يتغير عند تعديل رمز المدرسة.",
            ),
            preserve_default=False,
        ),
        migrations.RunPython(populate_storage_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="school",
            name="storage_key",
            field=models.SlugField(
                editable=False,
                max_length=96,
                unique=True,
                verbose_name="مفتاح مجلد التخزين",
                help_text="معرّف ثابت لمجلد المدرسة في التخزين، ولا يتغير عند تعديل رمز المدرسة.",
            ),
        ),
    ]
