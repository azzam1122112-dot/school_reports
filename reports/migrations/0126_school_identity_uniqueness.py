from __future__ import annotations

import re

from django.db import migrations, models
from django.db.models.functions import Lower


_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_SCHOOL_NAME_TRANSLATION = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
        "ـ": "",
    }
)


def normalize_school_name_identity(value: str) -> str:
    text = (value or "").strip().casefold()
    text = text.translate(_SCHOOL_NAME_TRANSLATION)
    text = _ARABIC_DIACRITICS_RE.sub("", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()


def normalize_sa_mobile_identity(value: str) -> str:
    phone = (value or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    phone = "".join(character for character in phone if character.isdigit())
    if phone.startswith("9665") and len(phone) == 12:
        return f"0{phone[3:]}"
    if phone.startswith("5") and len(phone) == 9:
        return f"0{phone}"
    return phone


def backfill_identity_fields(apps, schema_editor):
    School = apps.get_model("reports", "School")
    Teacher = apps.get_model("reports", "Teacher")

    for school in School.objects.all().only("pk", "name", "phone", "email").iterator(chunk_size=500):
        School.objects.filter(pk=school.pk).update(
            normalized_name=normalize_school_name_identity(school.name),
            phone=normalize_sa_mobile_identity(school.phone) if school.phone else school.phone,
            email=(school.email or "").strip().lower(),
        )

    for teacher in Teacher.objects.exclude(email="").only("pk", "email").iterator(chunk_size=500):
        Teacher.objects.filter(pk=teacher.pk).update(email=(teacher.email or "").strip().lower())


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0125_school_storage_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="normalized_name",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                editable=False,
                help_text="نسخة مطبّعة من الاسم لمنع تسجيل المدرسة أكثر من مرة.",
                max_length=220,
                verbose_name="اسم المدرسة للتقييد",
            ),
        ),
        migrations.RunPython(backfill_identity_fields, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="school",
            constraint=models.UniqueConstraint(
                fields=("normalized_name",),
                condition=~models.Q(normalized_name=""),
                name="uniq_school_normalized_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="school",
            constraint=models.UniqueConstraint(
                fields=("phone",),
                condition=models.Q(phone__isnull=False) & ~models.Q(phone=""),
                name="uniq_school_phone",
            ),
        ),
        migrations.AddConstraint(
            model_name="school",
            constraint=models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="uniq_school_email_ci",
            ),
        ),
        migrations.AddConstraint(
            model_name="teacher",
            constraint=models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="uniq_teacher_email_ci",
            ),
        ),
    ]
