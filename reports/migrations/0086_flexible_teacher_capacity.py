from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0085_remove_schooladditionrequest_subscription_preference"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsubscription",
            name="teacher_limit_override",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="تُستخدم للسعات المرنة. عند تركها فارغة يُطبق حد المعلمين الموجود في الباقة.",
                null=True,
                verbose_name="سعة المعلمين المشتراة",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="requested_teacher_limit",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="لقطة للسعة المرنة التي اختارتها المدرسة وقت إنشاء طلب الدفع.",
                null=True,
                verbose_name="سعة المعلمين المطلوبة",
            ),
        ),
    ]
