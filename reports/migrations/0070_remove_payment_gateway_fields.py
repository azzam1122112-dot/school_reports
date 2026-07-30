from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0069_payment_tap_gateway_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="payment",
            name="gateway_completed_at",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="gateway_message",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="gateway_response_code",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="gateway_status",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="payment_method",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="transaction_id",
        ),
    ]
