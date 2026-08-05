from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0078_leadershipevidencereport"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("bank_transfer", "تحويل بنكي"),
                    ("tamara", "تمارا"),
                    ("moyasar", "ميّسر"),
                ],
                db_index=True,
                default="bank_transfer",
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
    ]

