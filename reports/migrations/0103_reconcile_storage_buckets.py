"""يصلح أرقام الدلوين للبيانات القائمة، بلا أي قراءة شبكية للتخزين.

أمران كانا خاطئين قبل هذا الترحيل:

1. أحجام ملفات ``Document`` لم تكن تدخل في ``School.storage_used_bytes`` رغم أن
   رفعها يُفحص أمام حدّ مساحة العمل — فمساحة تُستهلك ولا تظهر في أي عدّاد.
   الأحجام نفسها كانت محفوظة في ``Document.storage_bytes``، فالتصحيح جمعٌ من
   قاعدة البيانات لا مسحٌ للملفات.

2. ``SchoolArchiveAddon.storage_used_bytes`` كان يُملأ بإجمالي المدرسة كلها،
   بينما تُقاس به شاشة المنصة أمام حدّ الأرشفة. يُعاد ضبطه على حجم النسخ
   السنوية وحدها.
"""
from django.db import migrations
from django.db.models import Sum


def reconcile(apps, schema_editor):
    School = apps.get_model("reports", "School")
    Document = apps.get_model("reports", "Document")
    SchoolYearArchive = apps.get_model("reports", "SchoolYearArchive")
    SchoolArchiveAddon = apps.get_model("reports", "SchoolArchiveAddon")

    document_bytes = {
        row["school"]: int(row["total"] or 0)
        for row in Document.objects.values("school").annotate(total=Sum("storage_bytes"))
        if row["total"]
    }
    for school_id, extra in document_bytes.items():
        school = School.objects.filter(pk=school_id).only("storage_used_bytes").first()
        if school is None:
            continue
        School.objects.filter(pk=school_id).update(
            storage_used_bytes=int(school.storage_used_bytes or 0) + extra
        )

    snapshot_bytes = {
        row["school"]: int(row["total"] or 0)
        for row in SchoolYearArchive.objects.values("school").annotate(
            total=Sum("storage_bytes")
        )
    }
    for addon in SchoolArchiveAddon.objects.only("id", "school", "storage_used_bytes"):
        correct = snapshot_bytes.get(addon.school_id, 0)
        if int(addon.storage_used_bytes or 0) != correct:
            SchoolArchiveAddon.objects.filter(pk=addon.pk).update(
                storage_used_bytes=correct
            )


def noop(apps, schema_editor):
    """لا رجعة: الأرقام القديمة كانت خاطئة، وإعادتها تعيد الخطأ نفسه."""


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0102_storage_bucket_labels"),
    ]

    operations = [
        migrations.RunPython(reconcile, noop),
    ]
