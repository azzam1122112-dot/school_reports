from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
	Department,
	DepartmentMembership,
	Report,
	School,
	SchoolMembership,
	SchoolSubscription,
	SubscriptionPlan,
	Payment,
	Teacher,
	Ticket,
)


class PaymentApprovalAppliesRequestedPlanTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school-pay")
		self.plan_a = SubscriptionPlan.objects.create(name="Plan A", price=100, days_duration=30, is_active=True)
		self.plan_b = SubscriptionPlan.objects.create(name="Plan B", price=200, days_duration=90, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=self.plan_a, start_date=today, end_date=today)

		self.admin = Teacher.objects.create_superuser(phone="0599999999", name="Admin", password="pass")
		self.client.force_login(self.admin)

	def test_approving_payment_changes_subscription_plan(self):
		# 1x1 PNG (valid)
		png_bytes = (
			b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
			b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x1d\xa4\x16\x00\x00\x00\x00IEND\xaeB`\x82"
		)
		receipt = SimpleUploadedFile("r.png", png_bytes, content_type="image/png")

		payment = Payment.objects.create(
			school=self.school,
			subscription=self.school.subscription,
			requested_plan=self.plan_b,
			amount="200.00",
			receipt_image=receipt,
			created_by=self.admin,
		)

		url = reverse("reports:platform_payment_detail", args=[payment.pk])
		res = self.client.post(url, {"status": Payment.Status.APPROVED, "notes": "ok"})
		self.assertEqual(res.status_code, 302)

		self.school.refresh_from_db()
		sub = self.school.subscription
		self.assertEqual(sub.plan_id, self.plan_b.id)


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
		self.dept_a = Department.objects.create(school=self.school_a, name="IT", slug="it", is_active=True)
		DepartmentMembership.objects.create(department=self.dept_a, teacher=self.user)

		# منشئ تذكرة في مدرسة B
		self.creator_b = Teacher.objects.create_user(phone="0500000003", name="Creator B", password="pass")
		SchoolMembership.objects.create(
			school=self.school_b,
			teacher=self.creator_b,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

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

	def test_role_manager_without_membership_is_forbidden(self):
		"""Regression: Role.slug=manager وحده لا يجب أن يمنح صلاحيات مدير المدرسة."""
		from .models import Role

		school = School.objects.create(name="School X", code="school-x")
		plan = SubscriptionPlan.objects.create(name="Test2", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=school, plan=plan, start_date=today, end_date=today)

		mgr_role, _ = Role.objects.get_or_create(
			slug="manager",
			defaults={"name": "مدير", "is_staff_by_default": True},
		)
		fake_manager = Teacher.objects.create_user(phone="0500000099", name="Fake Manager", password="pass")
		fake_manager.role = mgr_role
		fake_manager.save()

		self.client.force_login(fake_manager)
		session = self.client.session
		session["active_school_id"] = school.id
		session.save()

		url = reverse("reports:departments_list")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 302)
		self.assertIn(reverse("reports:home"), res["Location"])

	def test_tickets_inbox_does_not_leak_by_slug_across_schools(self):
		"""إذا كان للمستخدم عضوية قسم slug=it في مدرسة A ثم اختار مدرسة B، لا يجب أن تظهر تذاكر قسم it في مدرسة B."""
		dept_b = Department.objects.create(school=self.school_b, name="IT B", slug="it", is_active=True)
		ticket_b = Ticket.objects.create(
			school=self.school_b,
			creator=self.creator_b,
			department=dept_b,
			title="B-IT-TICKET-UNIQUE",
			body="hello",
			is_platform=False,
		)

		self.client.force_login(self.user)
		session = self.client.session
		session["active_school_id"] = self.school_b.id
		session.save()

		url = reverse("reports:tickets_inbox")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, ticket_b.title)

	def test_assigned_to_me_does_not_leak_by_slug_across_schools(self):
		"""نفس سيناريو التسريب عبر assigned_to_me (تذاكر غير مسندة + نفس slug)."""
		dept_b = Department.objects.create(school=self.school_b, name="IT B", slug="it", is_active=True)
		ticket_b = Ticket.objects.create(
			school=self.school_b,
			creator=self.creator_b,
			department=dept_b,
			title="B-ASSIGNED-LEAK-UNIQUE",
			body="hello",
			is_platform=False,
			assignee=None,
		)

		self.client.force_login(self.user)
		session = self.client.session
		session["active_school_id"] = self.school_b.id
		session.save()

		url = reverse("reports:assigned_to_me")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, ticket_b.title)

	def test_manager_can_print_report_in_active_school(self):
		"""Regression: طباعة التقرير يجب أن تعمل للمدير ضمن المدرسة النشطة."""
		from .models import ReportType

		rt = ReportType.objects.create(name="Type A", code="type-a", is_active=True, school=self.school_a)
		teacher_a = Teacher.objects.create_user(phone="0500000009", name="Teacher A", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=teacher_a,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		rep = Report.objects.create(
			school=self.school_a,
			teacher=teacher_a,
			title="R1",
			report_date=timezone.localdate(),
			category=rt,
		)

		self.client.force_login(self.user)
		session = self.client.session
		session["active_school_id"] = self.school_a.id
		session.save()

		url = reverse("reports:report_print", args=[rep.pk])
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)


class ReportViewerLimitTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school")
		# تفعيل اشتراك حتى لا تُرفض بعض المسارات/الإنشاءات في أماكن أخرى
		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

	def test_max_two_active_report_viewers_per_school(self):
		v1 = Teacher.objects.create_user(phone="0500000101", name="V1", password="pass")
		v2 = Teacher.objects.create_user(phone="0500000102", name="V2", password="pass")
		v3 = Teacher.objects.create_user(phone="0500000103", name="V3", password="pass")

		SchoolMembership.objects.create(
			school=self.school,
			teacher=v1,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=v2,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)

		with self.assertRaises(Exception):
			SchoolMembership.objects.create(
				school=self.school,
				teacher=v3,
				role_type=SchoolMembership.RoleType.REPORT_VIEWER,
				is_active=True,
			)

	def test_reactivating_viewer_enforces_limit(self):
		v1 = Teacher.objects.create_user(phone="0500000201", name="V1", password="pass")
		v2 = Teacher.objects.create_user(phone="0500000202", name="V2", password="pass")
		v3 = Teacher.objects.create_user(phone="0500000203", name="V3", password="pass")

		m1 = SchoolMembership.objects.create(
			school=self.school,
			teacher=v1,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=v2,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)
		m3 = SchoolMembership.objects.create(
			school=self.school,
			teacher=v3,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=False,
		)

		# تعطيل واحد ثم تفعيل الثالث يجب أن ينجح
		m1.is_active = False
		m1.save(update_fields=["is_active"])
		m3.is_active = True
		m3.save(update_fields=["is_active"])

		# إعادة تفعيل الأول الآن يجب أن تفشل لأننا سنصبح 3 نشطين
		m1.is_active = True
		with self.assertRaises(Exception):
			m1.save(update_fields=["is_active"])
