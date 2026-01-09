from __future__ import annotations

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
from django.utils import timezone


def disable_report_viewers(apps, schema_editor):
    """Disable legacy 'report viewer' memberships and deactivate standalone accounts.

    The product decision is to remove the report-viewer role entirely.
    We keep historical rows, but revoke access by disabling memberships.
    """

    Teacher = apps.get_model("reports", "Teacher")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")

    # 1) Disable report_viewer memberships
    rv_qs = SchoolMembership.objects.filter(role_type="report_viewer", is_active=True)
    rv_teacher_ids = list(rv_qs.values_list("teacher_id", flat=True).distinct())
    rv_qs.update(is_active=False)

    if not rv_teacher_ids:
        return

    # 2) Deactivate accounts that have no other active memberships
    other_active = (
        SchoolMembership.objects.filter(teacher_id__in=rv_teacher_ids, is_active=True)
        .exclude(role_type="report_viewer")
        .values_list("teacher_id", flat=True)
        .distinct()
    )
    other_active_ids = set(other_active)

    to_deactivate = [tid for tid in rv_teacher_ids if tid not in other_active_ids]
    if to_deactivate:
        Teacher.objects.filter(id__in=to_deactivate).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0028_payment_requested_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacher",
            name="is_platform_admin",
            field=models.BooleanField(default=False, verbose_name="مشرف عام للمنصة؟"),
        ),
        migrations.CreateModel(
            name="PlatformAdminScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "gender_scope",
                    models.CharField(
                        choices=[("all", "الجميع"), ("boys", "بنين"), ("girls", "بنات")],
                        default="all",
                        max_length=8,
                        verbose_name="نطاق بنين/بنات",
                    ),
                ),
                (
                    "allowed_cities",
                    models.JSONField(default=list, blank=True, verbose_name="المدن المسموحة"),
                ),
                (
                    "admin",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="platform_scope",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="المشرف العام",
                    ),
                ),
                (
                    "allowed_schools",
                    models.ManyToManyField(
                        blank=True,
                        related_name="platform_admins",
                        to="reports.school",
                        verbose_name="مدارس محددة (اختياري)",
                    ),
                ),
            ],
            options={
                "verbose_name": "نطاق مشرف عام",
                "verbose_name_plural": "نطاقات المشرفين العامين",
            },
        ),
        migrations.CreateModel(
            name="TeacherPrivateComment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField(verbose_name="التعليق")),
                ("created_at", models.DateTimeField(default=timezone.now, verbose_name="تاريخ الإضافة")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="private_comments_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="أضيف بواسطة",
                    ),
                ),
                (
                    "teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="private_comments_received",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="المعلم المستهدف",
                    ),
                ),
                (
                    "school",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="private_comments",
                        to="reports.school",
                        verbose_name="المدرسة",
                    ),
                ),
                (
                    "achievement_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="private_comments",
                        to="reports.teacherachievementfile",
                        verbose_name="ملف الإنجاز (اختياري)",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="private_comments",
                        to="reports.report",
                        verbose_name="التقرير (اختياري)",
                    ),
                ),
            ],
            options={
                "verbose_name": "تعليق خاص للمعلم",
                "verbose_name_plural": "تعليقات خاصة للمعلمين",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.RunPython(disable_report_viewers, migrations.RunPython.noop),
    ]
