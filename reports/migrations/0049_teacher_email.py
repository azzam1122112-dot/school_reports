from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0048_alter_platformadminscope_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254, verbose_name="البريد الإلكتروني"),
        ),
    ]
