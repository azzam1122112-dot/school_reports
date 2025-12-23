from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import School, Teacher, SchoolMembership, Department, SubscriptionPlan, SchoolSubscription


class ResolveDepartmentForCategoryTests(TestCase):
	def test_resolve_department_scoped_by_school(self):
		from .models import ReportType
		from .utils import _resolve_department_for_category

		school_a = School.objects.create(name="School A", code="sa")
		school_b = School.objects.create(name="School B", code="sb")

		rt_a = ReportType.objects.create(name="Type A", code="type-a", is_active=True, school=school_a)
		rt_b = ReportType.objects.create(name="Type B", code="type-b", is_active=True, school=school_b)

		dept_a = Department.objects.create(school=school_a, name="Dept A", slug="dept", is_active=True)
		dept_b = Department.objects.create(school=school_b, name="Dept B", slug="dept", is_active=True)

		dept_a.reporttypes.add(rt_a)
		dept_b.reporttypes.add(rt_b)

		self.assertEqual(_resolve_department_for_category(rt_a, school_a).pk, dept_a.pk)
		self.assertEqual(_resolve_department_for_category(rt_b, school_b).pk, dept_b.pk)


class TenantIsolationTests(TestCase):
	def setUp(self):
		self.school_a = School.objects.create(name="School A", code="school-a")
		self.school_b = School.objects.create(name="School B", code="school-b")

		# تفعيل اشتراكات حتى لا يعترض SubscriptionMiddleware طلبات الاختبار
		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school_a, plan=plan, start_date=today, end_date=today)
		SchoolSubscription.objects.create(school=self.school_b, plan=plan, start_date=today, end_date=today)

		self.user = Teacher.objects.create_user(phone="0500000001", name="Manager A", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=self.user,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		# قسم في مدرسة A لضمان وجود slug صالح للاختبار
		Department.objects.create(school=self.school_a, name="IT", slug="it", is_active=True)

	def test_api_department_members_requires_active_school_when_multi_school(self):
		# مستخدم بدون أي عضوية مدرسة: يجب منع الوصول عندما توجد مدارس مفعّلة
		lonely = Teacher.objects.create_user(phone="0500000002", name="No Membership", password="pass")
		self.client.force_login(lonely)

		url = reverse("reports:api_department_members")
		res = self.client.get(url, {"department": "it"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
		self.assertEqual(res.status_code, 403)

	def test_api_department_members_forbids_when_user_not_member_in_active_school(self):
		self.client.force_login(self.user)

		session = self.client.session
		session["active_school_id"] = self.school_b.id
		session.save()

		url = reverse("reports:api_department_members")
		res = self.client.get(url, {"department": "it"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
		self.assertEqual(res.status_code, 403)

	def test_manager_views_redirect_to_select_school_when_missing_active_school(self):
		self.client.force_login(self.user)

		url = reverse("reports:departments_list")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 302)
		self.assertIn(reverse("reports:select_school"), res["Location"])
