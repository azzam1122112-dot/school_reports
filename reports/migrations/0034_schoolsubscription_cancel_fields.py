from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0033_merge_20260109_2216"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsubscription",
            name="canceled_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="تاريخ الإلغاء",
                help_text="يُعبّأ عند إلغاء الاشتراك من مدير النظام.",
            ),
        ),
        migrations.AddField(
            model_name="schoolsubscription",
            name="cancel_reason",
            field=models.TextField(
                blank=True,
                verbose_name="سبب الإلغاء",
                help_text="يظهر للمدرسة عند إلغاء الاشتراك.",
            ),
        ),
    ]
