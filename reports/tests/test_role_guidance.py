from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Department,
    DepartmentMembership,
    ReportType,
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolMembership,
    SchoolSubscription,
    StaffScope,
    SubscriptionPlan,
    Teacher,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(
        name=name,
        phone=phone,
        email=f"{phone}@example.com",
        password="StrongPass123!",
    )


def _school(name: str, code: str) -> School:
    return School.objects.create(
        name=name,
        code=code,
        city="الرياض",
        phone="0110000000",
        current_academic_year="1448",
        is_active=True,
    )


@override_settings(ALLOWED_HOSTS=["testserver"])
class RoleGuidanceTests(TestCase):
    def setUp(self):
        self.school = _school("مدرسة الإرشاد", "guidance-school")
        self.manager = _user("مدير المدرسة", "0509000001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="خطة الإرشاد",
            price=0,
            days_duration=30,
            max_teachers=10,
            is_active=True,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
            is_active=True,
        )

    def _enter(self, user, school=None):
        self.client.force_login(user)
        if school is not None:
            session = self.client.session
            session["active_school_id"] = school.pk
            session.save()

    def test_manager_center_explains_blocking_dependencies(self):
        self._enter(self.manager, self.school)

        response = self.client.get(reverse("reports:role_guidance"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "صحة المدرسة")
        self.assertContains(response, "إنشاء الطلبات يعتمد على وجود قسم نشط")
        self.assertContains(response, "لن يظهر نموذج تقرير قابل للاستخدام")

    def test_school_health_reaches_full_readiness_from_real_configuration(self):
        employee = _user("موظف إداري", "0509000002")
        membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=employee,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
        )
        StaffScope.objects.create(membership=membership, granted_by=self.manager)
        report_type = ReportType.objects.create(
            school=self.school,
            name="تقرير عام",
            code="general",
            is_active=True,
        )
        department = Department.objects.create(
            school=self.school,
            name="القسم الإداري",
            slug="administration",
            is_active=True,
        )
        department.reporttypes.add(report_type)
        DepartmentMembership.objects.create(
            department=department,
            teacher=employee,
            role_type=DepartmentMembership.OFFICER,
        )
        self._enter(self.manager, self.school)

        response = self.client.get(reverse("reports:school_health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["health"]["percent"], 100)
        self.assertContains(response, "7 من 7 عناصر جاهزة")

    def test_teacher_sees_their_journey_and_cannot_open_school_health(self):
        teacher = _user("معلم", "0509000003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self._enter(teacher, self.school)

        center = self.client.get(reverse("reports:role_guidance"))
        health = self.client.get(reverse("reports:school_health"))

        self.assertEqual(center.status_code, 200)
        self.assertContains(center, "رحلة المعلم")
        self.assertContains(center, "إذا لم يظهر نوع تقرير")
        self.assertEqual(health.status_code, 302)

    def test_executive_center_uses_group_scope_without_active_school(self):
        executive = _user("مدير تنفيذي", "0509000004")
        group = SchoolGroup.objects.create(name="مجموعة مدارس", code="guidance-group")
        SchoolGroupMembership.objects.create(
            group=group,
            user=executive,
            role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR,
            is_active=True,
        )
        self.school.group = group
        self.school.save(update_fields=["group"])
        self._enter(executive)

        response = self.client.get(reverse("reports:role_guidance"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "رحلة المدير التنفيذي")
        self.assertContains(response, "التقرير التنفيذي المقارن")

    def test_platform_admin_gets_platform_operations_journey(self):
        admin = _user("مدير المنصة", "0509000005")
        admin.is_staff = True
        admin.is_superuser = True
        admin.save(update_fields=["is_staff", "is_superuser"])
        self._enter(admin)

        response = self.client.get(reverse("reports:role_guidance"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تشغيل المنصة")
        self.assertContains(response, "تفعيل الاشتراكات")
