# -*- coding: utf-8 -*-
"""عقد الـAPI: العزل، والنطاق، وألا تتقادم الوثيقة.

الـAPI سطحٌ يُستعمل بلا متصفّح وبلا إنسان يراقب، فأخطاؤه لا تُكتشف بالنظر.
والفحوص هنا تُقسَّم إلى:

* **العزل** — مفتاح مدرسةٍ لا يقرأ ولا يكتب في مدرسةٍ أخرى، مهما فعل بالحمولة.
* **النطاق** — مفتاح القراءة لا يكتب.
* **الحياة** — المفتاح المُبطَل أو المنتهي أو الذي عُطِّل صاحبه يُرفض فوراً.
* **الوثيقة** — كل مسار مُوجَّه موصوفٌ فيها، فلا تتقادم بصمت.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.api_schema import build_openapi_schema
from reports.model_parts.api_keys import generate_api_key
from reports.models import (
    Report,
    ReportType,
    School,
    SchoolApiKey,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


def _mint(school, actor, *, scope=SchoolApiKey.Scope.READ, **kwargs) -> tuple[str, SchoolApiKey]:
    raw, public_id, key_hash = generate_api_key()
    key = SchoolApiKey.objects.create(
        school=school, name="تكامل اختبار", public_id=public_id,
        key_hash=key_hash, scope=scope, acting_as=actor, **kwargs,
    )
    return raw, key


@override_settings(ALLOWED_HOSTS=["testserver"], DEBUG=True)
class ApiKeyAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=20
        )
        cls.mine = School.objects.create(name="مدرستي", code="api-mine")
        cls.theirs = School.objects.create(name="مدرسة أخرى", code="api-theirs")
        SchoolSubscription.objects.create(school=cls.mine, plan=plan)
        SchoolSubscription.objects.create(school=cls.theirs, plan=plan)

        cls.manager = Teacher.objects.create_user(
            phone="500800001", name="مدير", password="pass"
        )
        SchoolMembership.objects.create(
            school=cls.mine, teacher=cls.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        cls.other_manager = Teacher.objects.create_user(
            phone="500800002", name="مدير آخر", password="pass"
        )
        SchoolMembership.objects.create(
            school=cls.theirs, teacher=cls.other_manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        cls.kind = ReportType.objects.create(name="نشاط", code="api-kind", school=cls.mine)
        cls.their_kind = ReportType.objects.create(
            name="نشاطهم", code="api-kind-2", school=cls.theirs
        )
        Report.objects.create(
            school=cls.mine, teacher=cls.manager, category=cls.kind,
            title="تقرير مدرستي", idea="فكرة", report_date="2026-07-01",
        )
        Report.objects.create(
            school=cls.theirs, teacher=cls.other_manager, category=cls.their_kind,
            title="تقرير مدرستهم", idea="فكرة", report_date="2026-07-02",
        )

    def _get(self, url, raw_key=None, **kwargs):
        headers = {"HTTP_AUTHORIZATION": f"Api-Key {raw_key}"} if raw_key else {}
        return self.client.get(url, **headers, **kwargs)

    # ── المصادقة ────────────────────────────────────────────────────────

    def test_a_valid_key_authenticates(self):
        raw, _ = _mint(self.mine, self.manager)
        response = self._get("/api/v1/reports/", raw)

        self.assertEqual(response.status_code, 200)

    def test_no_key_is_rejected(self):
        response = self.client.get("/api/v1/reports/")
        self.assertIn(response.status_code, {401, 403})

    def test_an_unknown_key_is_rejected(self):
        response = self._get("/api/v1/reports/", "twq_deadbeef_notarealsecretvalue")
        self.assertEqual(response.status_code, 401)

    def test_a_revoked_key_stops_working_immediately(self):
        raw, key = _mint(self.mine, self.manager)
        self.assertEqual(self._get("/api/v1/reports/", raw).status_code, 200)

        key.is_active = False
        key.save(update_fields=["is_active"])

        self.assertEqual(self._get("/api/v1/reports/", raw).status_code, 401)

    def test_an_expired_key_is_rejected(self):
        raw, _ = _mint(
            self.mine, self.manager, expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self._get("/api/v1/reports/", raw).status_code, 401)

    def test_deactivating_the_person_kills_their_key(self):
        """مفتاحٌ يبقى عاملاً بعد خروج صاحبه تصعيدُ امتيازٍ صامت."""
        raw, _ = _mint(self.mine, self.manager)
        self.manager.is_active = False
        self.manager.save(update_fields=["is_active"])

        self.assertEqual(self._get("/api/v1/reports/", raw).status_code, 401)
        self.manager.is_active = True
        self.manager.save(update_fields=["is_active"])

    def test_the_raw_key_is_never_stored(self):
        raw, key = _mint(self.mine, self.manager)

        self.assertNotEqual(key.key_hash, raw)
        self.assertNotIn(raw, str(key.__dict__))
        self.assertFalse(SchoolApiKey.objects.filter(key_hash=raw).exists())

    # ── العزل ───────────────────────────────────────────────────────────

    def test_a_key_reads_only_its_own_school(self):
        raw, _ = _mint(self.mine, self.manager)
        titles = [row["title"] for row in self._get("/api/v1/reports/", raw).json()["results"]]

        self.assertIn("تقرير مدرستي", titles)
        self.assertNotIn("تقرير مدرستهم", titles)

    def test_a_key_cannot_reach_another_schools_record_by_id(self):
        raw, _ = _mint(self.mine, self.manager)
        theirs = Report.objects.get(title="تقرير مدرستهم")

        response = self._get(f"/api/v1/reports/{theirs.pk}/", raw)
        self.assertEqual(response.status_code, 404)

    def test_the_session_school_cannot_override_the_keys_school(self):
        """المدرسة تُشتقّ من المفتاح لا من الطلب."""
        raw, _ = _mint(self.mine, self.manager)
        session = self.client.session
        session["active_school_id"] = self.theirs.id
        session.save()

        titles = [row["title"] for row in self._get("/api/v1/reports/", raw).json()["results"]]
        self.assertNotIn("تقرير مدرستهم", titles)

    # ── النطاق ──────────────────────────────────────────────────────────

    def test_a_read_key_cannot_create(self):
        raw, _ = _mint(self.mine, self.manager, scope=SchoolApiKey.Scope.READ)
        response = self.client.post(
            "/api/v1/reports/",
            {"title": "محاولة كتابة", "report_date": "2026-08-01"},
            HTTP_AUTHORIZATION=f"Api-Key {raw}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Report.objects.filter(title="محاولة كتابة").exists())

    def test_a_write_key_can_create_a_report(self):
        raw, _ = _mint(self.mine, self.manager, scope=SchoolApiKey.Scope.WRITE)
        response = self.client.post(
            "/api/v1/reports/",
            {
                "title": "تقرير من التكامل",
                "report_date": "2026-08-01",
                "category": self.kind.pk,
                "idea": "وصف",
            },
            HTTP_AUTHORIZATION=f"Api-Key {raw}",
        )

        self.assertEqual(response.status_code, 201, response.content)
        report = Report.objects.get(title="تقرير من التكامل")
        # المدرسة والمُعِدّ من المفتاح لا من الحمولة.
        self.assertEqual(report.school, self.mine)
        self.assertEqual(report.teacher, self.manager)

    def test_a_write_key_cannot_plant_a_report_in_another_school(self):
        """أسهل اختراقٍ ممكن للعزل: تغييرُ رقم في JSON."""
        raw, _ = _mint(self.mine, self.manager, scope=SchoolApiKey.Scope.WRITE)
        response = self.client.post(
            "/api/v1/reports/",
            {
                "title": "زرعٌ في مدرسة أخرى",
                "report_date": "2026-08-01",
                "school": self.theirs.pk,
                "teacher": self.other_manager.pk,
            },
            HTTP_AUTHORIZATION=f"Api-Key {raw}",
        )

        self.assertEqual(response.status_code, 201, response.content)
        planted = Report.objects.get(title="زرعٌ في مدرسة أخرى")
        self.assertEqual(planted.school, self.mine)
        self.assertEqual(planted.teacher, self.manager)

    def test_a_category_from_another_school_is_refused(self):
        raw, _ = _mint(self.mine, self.manager, scope=SchoolApiKey.Scope.WRITE)
        response = self.client.post(
            "/api/v1/reports/",
            {
                "title": "نوع من مدرسة أخرى",
                "report_date": "2026-08-01",
                "category": self.their_kind.pk,
            },
            HTTP_AUTHORIZATION=f"Api-Key {raw}",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Report.objects.filter(title="نوع من مدرسة أخرى").exists())

    def test_last_used_is_recorded(self):
        raw, key = _mint(self.mine, self.manager)
        self.assertIsNone(key.last_used_at)

        self._get("/api/v1/reports/", raw)

        key.refresh_from_db()
        self.assertIsNotNone(key.last_used_at)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ApiKeyTransportTests(TestCase):
    def test_a_key_is_refused_over_plain_http_outside_debug(self):
        """المفتاح سرٌّ يُرسَل نصاً في كل طلب."""
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=5
        )
        school = School.objects.create(name="مدرسة", code="api-http")
        SchoolSubscription.objects.create(school=school, plan=plan)
        actor = Teacher.objects.create_user(
            phone="500800009", name="مدير", password="pass"
        )
        SchoolMembership.objects.create(
            school=school, teacher=actor, role_type=SchoolMembership.RoleType.MANAGER
        )
        raw, _ = _mint(school, actor)

        with override_settings(DEBUG=False):
            response = self.client.get(
                "/api/v1/reports/", HTTP_AUTHORIZATION=f"Api-Key {raw}"
            )
        self.assertEqual(response.status_code, 401)


@override_settings(ALLOWED_HOSTS=["testserver"])
class OpenApiSchemaTests(TestCase):
    def test_the_schema_endpoint_is_public_and_valid(self):
        response = self.client.get(reverse("openapi_schema"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["openapi"], "3.0.3")
        self.assertIn("ApiKeyAuth", payload["components"]["securitySchemes"])

    def test_every_routed_endpoint_is_documented(self):
        """الحارس الذي يمنع تقادم وثيقةٍ مكتوبة بيد.

        يقارن مسارات الموجِّه الفعلية بما في الوثيقة. فإن أُضيف مورد ولم
        يُوثَّق، يفشل هذا الاختبار قبل أن يكتشفه المتكامل.
        """
        from reports.api_urls import router

        documented = set(build_openapi_schema()["paths"])
        missing = []
        for prefix, _viewset, _basename in router.registry:
            for suffix in ("/", "/{id}/"):
                path = f"/{prefix}{suffix}"
                if path not in documented:
                    missing.append(path)

        self.assertEqual(missing, [], f"مسارات غير موصوفة في الوثيقة: {missing}")

    def test_the_schema_documents_the_write_path_and_its_scope(self):
        paths = build_openapi_schema()["paths"]

        self.assertIn("post", paths["/reports/"])
        self.assertIn("write", paths["/reports/"]["post"]["description"])

    def test_the_schema_carries_no_secret(self):
        """الوثيقة تصف صيغة المفتاح ولا تحمل مفتاحاً.

        الفحص على **شكل مفتاح حقيقي** لا على البادئة: ``twq_<id>_<secret>``
        في وصف المصادقة هو ما يحتاجه المتكامل ليعرف الصيغة، وحجبُه يجعل
        الوثيقة أنقص بلا أن يزيد أماناً.
        """
        import re

        blob = str(build_openapi_schema())

        real_key = re.compile(r"twq_[0-9a-f]{12}_[A-Za-z0-9_-]{20,}")
        self.assertIsNone(real_key.search(blob), "مفتاح حقيقي في الوثيقة")

        for forbidden in ("key_hash", "SECRET_KEY", "DATABASE_URL"):
            self.assertNotIn(forbidden, blob)
