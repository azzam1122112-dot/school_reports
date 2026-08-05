from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0075_payment_gateway_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformsettings",
            name="mansour_public_enabled",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="إظهار منصور للزوار في الصفحة العامة والسماح باستخدامه.",
                verbose_name="إظهار المساعد منصور",
            ),
        ),
        migrations.AddField(
            model_name="platformsettings",
            name="report_ai_enabled",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="إظهار أداة تحسين صياغة التقرير والسماح باستدعائها.",
                verbose_name="إظهار تحسين التقارير",
            ),
        ),
        migrations.AddField(
            model_name="platformsettings",
            name="internal_ai_help_enabled",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="إظهار أداة المساعدة العائمة داخل الصفحات بعد تسجيل الدخول.",
                verbose_name="إظهار المساعدة داخل النظام",
            ),
        ),
    ]
