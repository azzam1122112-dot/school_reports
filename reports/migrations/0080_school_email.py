from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0079_alter_payment_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="email",
            field=models.EmailField(
                blank=True,
                default="",
                max_length=254,
                verbose_name="البريد الإلكتروني",
            ),
        ),
    ]
