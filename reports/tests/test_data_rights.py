# -*- coding: utf-8 -*-
"""حقوق صاحب البيانات: نسخةٌ كاملة عنه، ولا حرفٌ عن غيره، ولا سرٌّ فيها.

سياسة الخصوصية تَعِد بـ«الوصول، وطلب نسخة مقروءة… وطلب الإتلاف في الحالات
المقررة». والوعد الآن منفَّذ في الكود، فيجب أن يُحرَس فيه:

* **الشمول** — نسخةٌ ناقصة تُخلّ بالحق الذي وُعد به.
* **الحصر** — بيانات شخصٍ آخر في نسختي تسريبٌ يرتكبه الحق نفسه.
* **الأسرار** — كلمة المرور ومفاتيح المصادقة ومفاتيح الدفع ليست «بيانات
  شخصية تُسلَّم»: تسليمها يخلق الخطر الذي جاء الحق ليحمي منه.
"""
from __future__ import annotations

import json

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    ErasureRequest,
    Notification,
    NotificationRecipient,
    Report,
    ReportType,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
    TeacherPrivateComment,
    Ticket,
    WebAuthnCredential,
)
from reports.services_data_rights import FORBIDDEN_KEYS, build_personal_data_export


def _walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def _walk_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_values(item)
    elif node is not None:
        yield str(node)


@override_settings(ALLOWED_HOSTS=["testserver"])
class PersonalDataExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=20
        )
        cls.school = School.objects.create(name="مدرسة", code="rights-school")
        SchoolSubscription.objects.create(school=cls.school, plan=plan)
        cls.category = ReportType.objects.create(
            name="نشاط", code="rights-kind", school=cls.school
        )

        cls.subject = Teacher.objects.create_user(
            phone="500600001", name="صاحب البيانات", password="secret-pass-1",
            national_id="1122334455", email="subject@example.com",
        )
        SchoolMembership.objects.create(
            school=cls.school, teacher=cls.subject,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        cls.other = Teacher.objects.create_user(
            phone="500600002", name="شخص آخر", password="secret-pass-2",
            national_id="9988776655",
        )
        SchoolMembership.objects.create(
            school=cls.school, teacher=cls.other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        Report.objects.create(
            school=cls.school, teacher=cls.subject, category=cls.category,
            title="تقريري أنا", idea="فكرة", report_date="2026-06-01",
        )
        Report.objects.create(
            school=cls.school, teacher=cls.other, category=cls.category,
            title="تقرير غيري", idea="فكرة", report_date="2026-06-02",
        )
        Ticket.objects.create(
            school=cls.school, creator=cls.subject, is_platform=False,
            title="طلبي أنا", body="نص",
        )
        Ticket.objects.create(
            school=cls.school, creator=cls.other, is_platform=False,
            title="طلب غيري", body="نص",
        )
        mine = Notification.objects.create(
            title="إشعار لي", message="نص", school=cls.school
        )
        NotificationRecipient.objects.create(notification=mine, teacher=cls.subject)
        theirs = Notification.objects.create(
            title="إشعار لغيري", message="نص", school=cls.school
        )
        NotificationRecipient.objects.create(notification=theirs, teacher=cls.other)

    # ── الشمول ──────────────────────────────────────────────────────────

    def test_the_export_contains_the_subjects_own_content(self):
        export = build_personal_data_export(self.subject)
        blob = json.dumps(export, ensure_ascii=False)

        self.assertEqual(export["sections"]["profile"]["name"], "صاحب البيانات")
        self.assertIn("تقريري أنا", blob)
        self.assertIn("طلبي أنا", blob)
        self.assertIn("إشعار لي", blob)
        self.assertEqual(export["incomplete_sections"], [])

    def test_every_declared_section_is_present(self):
        from reports.services_data_rights import SECTIONS

        export = build_personal_data_export(self.subject)
        for name, _builder in SECTIONS:
            self.assertIn(name, export["sections"], f"قسم ناقص: {name}")

    # ── الحصر ───────────────────────────────────────────────────────────

    def test_no_other_persons_content_appears(self):
        blob = json.dumps(build_personal_data_export(self.subject), ensure_ascii=False)

        self.assertNotIn("تقرير غيري", blob)
        self.assertNotIn("طلب غيري", blob)
        self.assertNotIn("إشعار لغيري", blob)
        self.assertNotIn("9988776655", blob)
        self.assertNotIn("شخص آخر", blob)

    # ── الأسرار ─────────────────────────────────────────────────────────

    def test_no_forbidden_key_appears_anywhere_in_the_export(self):
        export = build_personal_data_export(self.subject)
        leaked = sorted(set(_walk_keys(export)) & FORBIDDEN_KEYS)

        self.assertEqual(leaked, [], f"مفاتيح محظورة في النسخة: {leaked}")

    def test_the_password_hash_never_leaves(self):
        """تسليم التجزئة تسليمُ الحساب لمن يكسرها دون اتصال."""
        blob = json.dumps(build_personal_data_export(self.subject), ensure_ascii=False)

        self.subject.refresh_from_db()
        self.assertNotIn(self.subject.password, blob)
        self.assertNotIn("pbkdf2", blob)
        self.assertNotIn("bcrypt", blob)

    def test_passkey_material_is_withheld_but_its_existence_is_disclosed(self):
        WebAuthnCredential.objects.create(
            teacher=self.subject,
            credential_id=b"raw-credential-id",
            credential_id_hash="a" * 64,
            public_key_cose=b"super-secret-key-material",
            device_name="جوالي",
        )
        export = build_personal_data_export(self.subject)
        blob = json.dumps(export, ensure_ascii=False)

        # حق العلم محفوظ: يعرف أن لديه مفتاحاً وباسم جهازه.
        self.assertIn("جوالي", blob)
        # ومادة المصادقة لا تُسلَّم.
        self.assertNotIn("a" * 64, blob)
        self.assertNotIn("super-secret-key-material", blob)

    def test_private_notes_are_counted_but_not_quoted(self):
        """نصّ الملاحظة رأيُ طرفٍ آخر — يُعلَم بوجودها لا بمحتواها."""
        TeacherPrivateComment.objects.create(
            teacher=self.subject, created_by=self.other, school=self.school,
            body="ملاحظة إدارية حسّاسة جداً",
        )
        export = build_personal_data_export(self.subject)
        blob = json.dumps(export, ensure_ascii=False)

        self.assertEqual(export["sections"]["notes_about_me"]["count"], 1)
        self.assertNotIn("ملاحظة إدارية حسّاسة جداً", blob)

    def test_no_value_looks_like_a_django_session_or_hash(self):
        blob_values = list(_walk_values(build_personal_data_export(self.subject)))
        for value in blob_values:
            self.assertFalse(
                value.startswith(("pbkdf2_", "argon2", "bcrypt$")),
                f"قيمة تشبه تجزئة كلمة مرور: {value[:24]}",
            )


@override_settings(ALLOWED_HOSTS=["testserver"])
class DataRightsEndpointTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=10
        )
        self.school = School.objects.create(name="مدرسة", code="rights-endpoint")
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.user = Teacher.objects.create_user(
            phone="500700001", name="معلم", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school, teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_the_page_renders(self):
        response = self.client.get(reverse("reports:my_data"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تنزيل نسخة بياناتي")

    def test_download_is_an_attachment_and_never_cached(self):
        response = self.client.get(reverse("reports:my_data_download"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("noindex", response.headers["X-Robots-Tag"])
        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["subject"], "معلم")

    def test_the_download_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("reports:my_data_download"))

        self.assertIn(response.status_code, {302, 403})

    def test_an_erasure_request_is_recorded(self):
        response = self.client.post(
            reverse("reports:request_erasure"), {"reason": "لم أعد أعمل هنا"}
        )

        self.assertEqual(response.status_code, 302)
        record = ErasureRequest.objects.get(teacher=self.user)
        self.assertEqual(record.status, ErasureRequest.Status.RECEIVED)
        self.assertEqual(record.reason, "لم أعد أعمل هنا")

    def test_resending_does_not_create_a_second_open_request(self):
        """طلبان مفتوحان يُشتّتان المعالجة، والقيد في القاعدة يمنعهما."""
        self.client.post(reverse("reports:request_erasure"), {"reason": "أول"})
        self.client.post(reverse("reports:request_erasure"), {"reason": "ثانٍ"})

        self.assertEqual(ErasureRequest.objects.filter(teacher=self.user).count(), 1)

    def test_erasure_is_a_request_not_an_immediate_deletion(self):
        """الحساب يبقى: المحتوى مدرسي وسجلّ التدقيق مقصودٌ بقاؤه."""
        self.client.post(reverse("reports:request_erasure"), {"reason": "طلب"})

        self.user.refresh_from_db()
        self.assertTrue(Teacher.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(self.user.is_active)

    def test_the_request_form_is_hidden_while_one_is_open(self):
        self.client.post(reverse("reports:request_erasure"), {"reason": "طلب"})
        response = self.client.get(reverse("reports:my_data"))

        self.assertContains(response, "لديك طلب قائم")
        self.assertNotContains(response, "تسجيل طلب الإتلاف")
