from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0123_discount_codes")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("bank_transfer", "تحويل بنكي"),
                    ("moyasar", "ميّسر"),
                    ("tamara", "تمارا"),
                ],
                db_index=True,
                default="bank_transfer",
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
    ]
