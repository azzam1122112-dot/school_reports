from django.db import migrations, models


class Migration(migrations.Migration):
    """Drop Tamara from the payment-method choices.

    Choices are validation metadata, not a column constraint, so any historical
    row that still reads ``tamara`` survives this untouched — it simply renders
    as its raw value. Deleting those rows would destroy payment history, which
    is not something a code change should do silently.
    """

    dependencies = [
        ("reports", "0112_drop_weekly_summary_email_pref"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("bank_transfer", "تحويل بنكي"),
                    ("moyasar", "ميّسر"),
                ],
                db_index=True,
                default="bank_transfer",
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
    ]
