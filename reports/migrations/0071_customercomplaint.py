from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0070_remove_payment_gateway_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerComplaint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, verbose_name="الاسم")),
                ("email", models.EmailField(max_length=254, verbose_name="البريد الإلكتروني")),
                ("phone", models.CharField(blank=True, default="", max_length=30, verbose_name="رقم الجوال")),
                ("order_reference", models.CharField(blank=True, default="", max_length=100, verbose_name="مرجع الطلب أو الاشتراك")),
                ("subject", models.CharField(max_length=180, verbose_name="موضوع الشكوى")),
                ("message", models.TextField(max_length=5000, verbose_name="تفاصيل الشكوى")),
                ("status", models.CharField(choices=[("new", "جديدة"), ("in_progress", "قيد المعالجة"), ("resolved", "تمت المعالجة"), ("closed", "مغلقة")], db_index=True, default="new", max_length=20, verbose_name="الحالة")),
                ("internal_notes", models.TextField(blank=True, default="", verbose_name="ملاحظات المعالجة")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاريخ الاستلام")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="تاريخ المعالجة")),
            ],
            options={
                "verbose_name": "شكوى عميل",
                "verbose_name_plural": "شكاوى العملاء",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="customercomplaint",
            index=models.Index(fields=["status", "-created_at"], name="reports_cc_status_date_idx"),
        ),
    ]
