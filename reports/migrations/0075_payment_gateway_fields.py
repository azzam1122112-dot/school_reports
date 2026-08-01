from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0074_schoolleadershipportfolio_leadershipportfoliosection_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[("bank_transfer", "تحويل بنكي"), ("tamara", "تمارا")],
                db_index=True,
                default="bank_transfer",
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_order_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=160,
                verbose_name="رقم طلب بوابة الدفع",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="gateway_checkout_id",
            field=models.CharField(
                blank=True,
                default="",
                max_length=160,
                verbose_name="رقم جلسة بوابة الدفع",
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
            name="gateway_capture_id",
            field=models.CharField(
                blank=True,
                default="",
                max_length=160,
                verbose_name="رقم تحصيل بوابة الدفع",
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