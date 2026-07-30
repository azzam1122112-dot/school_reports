from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0068_update_archive_storage_price_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[("bank_transfer", "تحويل بنكي"), ("tap", "Tap")],
                db_index=True,
                default="bank_transfer",
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="transaction_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=160,
                verbose_name="رقم عملية بوابة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_status",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=32,
                verbose_name="حالة بوابة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_response_code",
            field=models.CharField(
                blank=True,
                default="",
                max_length=32,
                verbose_name="رمز رد بوابة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_message",
            field=models.CharField(
                blank=True,
                default="",
                max_length=255,
                verbose_name="رسالة بوابة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_completed_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="وقت اكتمال عملية البوابة",
            ),
        ),
    ]
