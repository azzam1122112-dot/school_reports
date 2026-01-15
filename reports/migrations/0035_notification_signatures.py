from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0034_schoolsubscription_cancel_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="requires_signature",
            field=models.BooleanField(
                default=False,
                help_text="عند التفعيل يصبح الإشعار تعميمًا ويتطلب إقرار + إدخال الجوال للتوقيع.",
                verbose_name="يتطلب توقيع؟",
            ),
        ),
        migrations.AddField(
            model_name="notification",
            name="signature_deadline_at",
            field=models.DateTimeField(
                blank=True,
                help_text="اختياري: يظهر للمعلمين في صفحة التوقيع ويستخدم للتقارير.",
                null=True,
                verbose_name="آخر موعد للتوقيع",
            ),
        ),
        migrations.AddField(
            model_name="notification",
            name="signature_ack_text",
            field=models.TextField(
                blank=True,
                default="أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به.",
                verbose_name="نص الإقرار",
            ),
        ),
        migrations.AddField(
            model_name="notificationrecipient",
            name="is_signed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="notificationrecipient",
            name="signed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationrecipient",
            name="signature_attempt_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="notificationrecipient",
            name="signature_last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="notificationrecipient",
            index=models.Index(fields=["teacher", "is_signed", "-created_at"], name="reports_noti_teacher_issigned_created_idx"),
        ),
    ]
