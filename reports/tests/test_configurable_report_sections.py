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
