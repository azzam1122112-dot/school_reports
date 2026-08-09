from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CrossSchoolRoleRoutingTests(TestCase):
    """One account may hold different roles in different schools."""

    def setUp(self):
        self.teaching_school = School.objects.create(
            name="مدرسة التدريس",
            code="cross-role-teaching",
        )
        self.managed_school = School.objects.create(
            name="مدرسة الإدارة",
            code="cross-role-managed",
        )
        plan = SubscriptionPlan.objects.create(
            name="باقة تعدد الأدوار",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.teaching_school, plan=plan)
        SchoolSubscription.objects.create(school=self.managed_school, plan=plan)

        self.user = Teacher.objects.create_user(
            phone="500170001",
            name="مدير ومعلم بين مدرستين",
            password="cross-role-pass",
            is_staff=True,
        )
        # Create teaching first to guard against routing that accidentally uses
        # the oldest membership instead of the role in the active school.
        SchoolMembership.objects.create(
            school=self.teaching_school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        SchoolMembership.objects.create(
            school=self.managed_school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

    def _activate(self, school):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = school.pk
        session.save()

    def test_home_uses_the_role_in_the_active_school(self):
        self._activate(self.teaching_school)
        teaching_home = self.client.get(reverse("reports:home"))
        self.assertEqual(teaching_home.status_code, 200)
        self.assertTemplateUsed(teaching_home, "reports/home.html")

        self._activate(self.managed_school)
        managed_home = self.client.get(reverse("reports:home"))
        self.assertRedirects(
            managed_home,
            reverse("reports:admin_dashboard"),
            fetch_redirect_response=False,
        )

    def test_header_offers_every_active_membership_school(self):
        self._activate(self.teaching_school)
        response = self.client.get(reverse("reports:home"))
        offered = {school.pk for school in response.context["USER_SCHOOLS"]}
        self.assertEqual(
            offered,
            {self.teaching_school.pk, self.managed_school.pk},
        )

    def test_switching_to_the_teaching_school_opens_teacher_home(self):
        self._activate(self.managed_school)
        response = self.client.post(
            reverse("reports:switch_school"),
            {"school_id": self.teaching_school.pk},
        )
        self.assertRedirects(
            response,
            reverse("reports:home"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.session["active_school_id"],
            self.teaching_school.pk,
        )

    def test_switching_to_the_managed_school_opens_manager_dashboard(self):
        self._activate(self.teaching_school)
        response = self.client.post(
            reverse("reports:switch_school"),
            {"school_id": self.managed_school.pk},
        )
        self.assertRedirects(
            response,
            reverse("reports:admin_dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.session["active_school_id"],
            self.managed_school.pk,
        )

    def test_login_prefers_an_active_managed_school(self):
        response = self.client.post(
            reverse("reports:login"),
            {"phone": self.user.phone, "password": "cross-role-pass"},
        )
        self.assertRedirects(
            response,
            reverse("reports:admin_dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.session["active_school_id"],
            self.managed_school.pk,
        )
