from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    Report,
    ReportType,
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ConfigurableReportSectionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(
            name="مدرسة البنود المرنة",
            code="configurable-report-sections",
        )
        plan = SubscriptionPlan.objects.create(
            name="خطة البنود المرنة",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        SchoolArchiveAddon.objects.create(
            school=self.school,
            is_enabled=True,
            storage_limit_gb=10,
        )
        self.teacher = Teacher.objects.create_user(
            phone="500088801",
            name="منشئ التقرير المرن",
            password="pass",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.category = ReportType.objects.create(
            school=self.school,
            code="flexible",
            name="تقرير مرن",
        )
        self.client.force_login(self.teacher)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_teacher_selects_sections_and_print_shows_only_selected_content(self):
        create_page = self.client.get(reverse("reports:add_report"))
        self.assertContains(create_page, "حدد محتوى تقريرك")
        self.assertContains(create_page, "آلية التنفيذ")
        self.assertContains(create_page, "التوصيات")

        response = self.client.post(
            reverse("reports:add_report"),
            {
                "section_selection_enabled": "True",
                "title": "تقرير مرن",
                "report_date": "2026-08-02",
                "category": self.category.code,
                "show_goal": "on",
                "goal": "رفع مستوى المشاركة الطلابية.",
                "show_results": "on",
                "results": "تحققت مشاركة واسعة من الطلاب.",
            },
        )

        report = Report.objects.get(title="تقرير مرن")
        self.assertRedirects(
            response,
            reverse("reports:my_reports"),
            fetch_redirect_response=False,
        )
        self.assertTrue(report.show_goal)
        self.assertTrue(report.show_results)
        self.assertFalse(report.show_details)
        self.assertFalse(report.show_beneficiaries)

        print_response = self.client.get(reverse("reports:report_print", args=[report.pk]))
        self.assertContains(print_response, "رفع مستوى المشاركة الطلابية")
        self.assertContains(print_response, "تحققت مشاركة واسعة من الطلاب")
        self.assertContains(print_response, self.teacher.name, count=1)
        self.assertNotContains(print_response, "تفاصيل التقرير</span>")
        self.assertNotContains(print_response, "عدد المستفيدين</th>")

    def test_selected_section_requires_its_content(self):
        response = self.client.post(
            reverse("reports:add_report"),
            {
                "section_selection_enabled": "True",
                "title": "تقرير ناقص",
                "report_date": "2026-08-02",
                "category": self.category.code,
                "show_recommendations": "on",
                "recommendations": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أدخل محتوى بند التوصيات")
        self.assertFalse(Report.objects.filter(title="تقرير ناقص").exists())

    def test_browser_shaped_empty_evidence_slots_do_not_block_report_creation(self):
        """The live editor posts four empty evidence rows with UI defaults."""
        payload = {
            "section_selection_enabled": "True",
            "title": "تقرير من المتصفح",
            "report_date": "2026-08-02",
            "category": self.category.code,
            "show_details": "on",
            "idea": "تفاصيل مكتملة من نموذج المتصفح.",
            "show_beneficiaries": "on",
            "beneficiaries_count": "12",
            "evidence-TOTAL_FORMS": "4",
            "evidence-INITIAL_FORMS": "0",
            "evidence-MIN_NUM_FORMS": "0",
            "evidence-MAX_NUM_FORMS": "8",
        }
        for index in range(4):
            payload[f"evidence-{index}-order"] = str(index + 1)
            payload[f"evidence-{index}-display_size"] = "auto"
            payload[f"evidence-{index}-fit_mode"] = "contain"
            payload[f"evidence-{index}-show_in_print"] = "on"

        response = self.client.post(reverse("reports:add_report"), payload)

        self.assertRedirects(
            response,
            reverse("reports:my_reports"),
            fetch_redirect_response=False,
        )
        report = Report.objects.get(title="تقرير من المتصفح")
        self.assertEqual(report.beneficiaries_count, 12)
        self.assertEqual(report.evidences.count(), 0)

    def test_xhr_validation_failure_returns_422_and_preserves_errors(self):
        response = self.client.post(
            reverse("reports:add_report"),
            {
                "section_selection_enabled": "True",
                "title": "تقرير ناقص عبر XHR",
                "report_date": "2026-08-02",
                "category": self.category.code,
                "show_results": "on",
                "results": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(
            response,
            "أدخل محتوى بند النتائج",
            status_code=422,
        )
        self.assertFalse(Report.objects.filter(title="تقرير ناقص عبر XHR").exists())
