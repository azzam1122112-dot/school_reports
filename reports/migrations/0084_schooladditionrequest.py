from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0083_subscriptionplan_commercial_entitlements"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SchoolAdditionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("school_name", models.CharField(max_length=200, verbose_name="اسم المدرسة المطلوبة")),
                ("stage", models.CharField(choices=[("kg", "رياض أطفال"), ("primary", "ابتدائي"), ("middle", "متوسط"), ("high", "ثانوي")], max_length=16, verbose_name="المرحلة")),
                ("gender", models.CharField(choices=[("boys", "بنين"), ("girls", "بنات")], max_length=8, verbose_name="بنين / بنات")),
                ("city", models.CharField(blank=True, default="", max_length=120, verbose_name="المدينة")),
                ("phone", models.CharField(blank=True, default="", max_length=20, verbose_name="جوال المدرسة")),
                ("email", models.EmailField(blank=True, default="", max_length=254, verbose_name="بريد المدرسة")),
                ("subscription_preference", models.CharField(choices=[("individual", "اشتراك مستقل للمدرسة"), ("group", "باقة مجموعة مدارس")], default="individual", max_length=16, verbose_name="طريقة الاشتراك المطلوبة")),
                ("manager_notes", models.TextField(blank=True, default="", max_length=1000, verbose_name="ملاحظات المدير")),
                ("status", models.CharField(choices=[("pending", "قيد المراجعة"), ("approved", "معتمد"), ("rejected", "مرفوض")], db_index=True, default="pending", max_length=16, verbose_name="الحالة")),
                ("review_notes", models.TextField(blank=True, default="", max_length=1000, verbose_name="ملاحظات المراجعة")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True, verbose_name="تاريخ المراجعة")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاريخ الطلب")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")),
                ("created_school", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_addition_request", to="reports.school", verbose_name="المدرسة المنشأة")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="school_addition_requests", to=settings.AUTH_USER_MODEL, verbose_name="مقدم الطلب")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_school_addition_requests", to=settings.AUTH_USER_MODEL, verbose_name="راجع الطلب")),
                ("source_school", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="addition_requests_from", to="reports.school", verbose_name="المدرسة الحالية")),
            ],
            options={
                "verbose_name": "طلب إضافة مدرسة",
                "verbose_name_plural": "طلبات إضافة المدارس",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="schooladditionrequest",
            index=models.Index(fields=["requested_by", "status"], name="reports_sar_user_status_idx"),
        ),
    ]
