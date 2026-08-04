from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0084_schooladditionrequest"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="schooladditionrequest",
            name="subscription_preference",
        ),
    ]
