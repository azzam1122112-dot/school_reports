from __future__ import annotations

from datetime import date

from django.test import TestCase

from reports.models import Report, ReportType, School, Teacher
from reports.services_reports import get_teacher_reports_queryset


class TeacherReportsQuerysetPerformanceTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Perf School", code="perf-school")
        self.report_type = ReportType.objects.create(
            school=self.school,
            name="Academic",
            code="academic",
            is_active=True,
        )
        self.teacher = Teacher.objects.create_user(
            phone="522222222",
            name="Perf Teacher",
            password="pass",
        )
        Report.objects.bulk_create(
            [
                Report(
                    school=self.school,
                    teacher=self.teacher,
                    teacher_name=self.teacher.name,
                    category=self.report_type,
                    title=f"Report {idx}",
                    report_date=date(2026, 1, min(idx + 1, 28)),
                    idea="Performance body",
                )
                for idx in range(8)
            ]
        )

    def test_teacher_reports_queryset_uses_joined_relations_without_n_plus_one(self):
        qs = get_teacher_reports_queryset(user=self.teacher, active_school=self.school)

        # One query loads reports and their joined relations; the second
        # prefetches all evidence rows for the page without an N+1 penalty.
        with self.assertNumQueries(2):
            rows = list(qs[:5])
            payload = [
                (
                    row.title,
                    row.teacher.name,
                    row.category.name if row.category_id else "",
                    row.school.name if row.school_id else "",
                )
                for row in rows
            ]

        self.assertEqual(len(payload), 5)
