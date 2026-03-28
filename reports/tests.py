from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from .middleware import FORCE_PASSWORD_CHANGE_SESSION_KEY
from .models import (
	Department,
	DepartmentMembership,
	Notification,
	NotificationRecipient,
	AuditLog,
	Report,
	ReportType,
	School,
	SchoolMembership,
	SchoolSubscription,
	SubscriptionPlan,
	Payment,
	PlatformAdminScope,
	Teacher,
	Ticket,
	TicketRecipient,
)
from .tasks import send_daily_manager_summary_task


class PlatformAdminCircularAndTicketPrintTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School A", code="pa-circ")
		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)
		self.manager = Teacher.objects.create_user(phone="0500000200", name="Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.platform = Teacher.objects.create_user(
			phone="0500000299",
			name="Platform",
			password="pass",
			is_platform_admin=True,
		)
		scope = PlatformAdminScope.objects.create(admin=self.platform)
		scope.allowed_schools.add(self.school)
		self.client.force_login(self.platform)

	def test_platform_admin_can_send_circular_without_target_school(self):
		url = reverse("reports:circulars_create")
		res = self.client.post(
			url,
			{
				"title": "Circular",
				"message": "Hello",
				"send_to_all_managers": "on",
				# intentionally omit target_school to ensure platform admin can send scoped-all
			},
		)
		self.assertEqual(res.status_code, 302)
		n = Notification.objects.order_by("-id").first()
		self.assertIsNotNone(n)
		self.assertTrue(bool(getattr(n, "requires_signature", False)))
		self.assertEqual(getattr(n, "created_by_id", None), self.platform.id)
		# "scoped-all" for platform admin circulars
		self.assertIsNone(getattr(n, "school_id", None))

	def test_platform_admin_can_print_ticket_details(self):
		creator = Teacher.objects.create_user(phone="0500000300", name="Creator", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=creator,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		t = Ticket.objects.create(
			school=self.school,
			creator=creator,
			title="Request",
			body="Details",
		)
		url = reverse("reports:ticket_print", args=[t.pk])
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)

	def test_platform_admin_can_send_notification(self):
		teacher = Teacher.objects.create_user(phone="0500000310", name="Teacher", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		url = reverse("reports:notifications_create")
		res = self.client.post(
			url,
			{
				"title": "Notify",
				"message": "Hello",
				"audience_scope": "school",
				"target_school": str(self.school.id),
				"teachers": [str(teacher.id)],
			},
		)
		self.assertEqual(res.status_code, 302)
		n = Notification.objects.order_by("-id").first()
		self.assertIsNotNone(n)
		self.assertFalse(bool(getattr(n, "requires_signature", False)))
		self.assertEqual(getattr(n, "created_by_id", None), self.platform.id)
		self.assertEqual(getattr(n, "school_id", None), self.school.id)


class PlatformAuditLogsRegressionTests(TestCase):
	def setUp(self):
		self.platform = Teacher.objects.create_superuser(
			phone="0500000350",
			name="Platform Audit",
			password="pass",
		)
		self.actor = Teacher.objects.create_user(
			phone="0500000351",
			name="Audit Actor",
			password="pass",
		)
		self.school = School.objects.create(name="Audit School", code="audit-platform")
		self.client.force_login(self.platform)

		for idx in range(55):
			AuditLog.objects.create(
				school=self.school,
				teacher=self.actor,
				action=AuditLog.Action.UPDATE,
				model_name="Report",
				object_id=idx + 1,
				object_repr=f"Audit log #{idx + 1}",
				ip_address="127.0.0.1",
			)

	def test_platform_audit_logs_pagination_links_skip_none_values(self):
		res = self.client.get(reverse("reports:platform_audit_logs"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, "?page=2")
		self.assertNotContains(res, "teacher=None")
		self.assertNotContains(res, "action=None")
		self.assertNotContains(res, "start_date=None")
		self.assertNotContains(res, "end_date=None")
		self.assertTrue(res.context["teachers"].filter(pk=self.actor.pk).exists())

	def test_platform_audit_logs_ignores_string_none_filters(self):
		res = self.client.get(
			reverse("reports:platform_audit_logs"),
			{
				"page": 2,
				"teacher": "None",
				"action": "None",
				"start_date": "None",
				"end_date": "None",
			},
		)
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.context["logs"].number, 2)
		self.assertEqual(res.context["q_teacher"], "")
		self.assertEqual(res.context["q_action"], "")
		self.assertEqual(res.context["q_start"], "")
		self.assertEqual(res.context["q_end"], "")
		self.assertGreater(len(res.context["logs"].object_list), 0)


class AdminReportsPaginationRegressionTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="Admin Reports School", code="admin-reports-school")
		plan = SubscriptionPlan.objects.create(name="Admin Reports Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.manager = Teacher.objects.create_user(phone="0500000352", name="Manager Reports", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.author = Teacher.objects.create_user(phone="0500000353", name="Report Author", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.author,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

		self.client.force_login(self.manager)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

		for idx in range(25):
			Report.objects.create(
				school=self.school,
				teacher=self.author,
				title=f"Report #{idx + 1}",
				report_date=today,
				idea="Regression coverage",
			)

	def test_admin_reports_pagination_links_skip_none_values(self):
		res = self.client.get(reverse("reports:admin_reports"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, "?page=2")
		self.assertNotContains(res, "start_date=None")
		self.assertNotContains(res, "end_date=None")
		self.assertNotContains(res, "teacher_name=None")
		self.assertNotContains(res, "category=None")

	def test_admin_reports_ignores_string_none_filters(self):
		res = self.client.get(
			reverse("reports:admin_reports"),
			{
				"page": 2,
				"start_date": "None",
				"end_date": "None",
				"teacher_name": "None",
				"category": "None",
			},
		)
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.context["reports"].number, 2)
		self.assertEqual(res.context["start_date"], "")
		self.assertEqual(res.context["end_date"], "")
		self.assertEqual(res.context["teacher_name"], "")
		self.assertEqual(res.context["category"], "")
		self.assertGreater(len(res.context["reports"].object_list), 0)


class ManagerNotificationDepartmentTargetingTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School A", code="mgr-dept")
		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.manager = Teacher.objects.create_user(phone="0500000400", name="Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.dept = Department.objects.create(school=self.school, name="Science", slug="science", is_active=True)

		self.t1 = Teacher.objects.create_user(phone="0500000401", name="T1", password="pass")
		self.t2 = Teacher.objects.create_user(phone="0500000402", name="T2", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.t1,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.t2,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		DepartmentMembership.objects.create(department=self.dept, teacher=self.t1)
		DepartmentMembership.objects.create(department=self.dept, teacher=self.t2)

		self.client.force_login(self.manager)

	def test_department_selected_but_manual_teacher_limits_recipients(self):
		url = reverse("reports:notifications_create")
		with self.captureOnCommitCallbacks(execute=True):
			res = self.client.post(
				url,
				{
					"title": "Notify",
					"message": "Hello",
					"target_department": str(self.dept.id),
					"teachers": [str(self.t1.id)],
				},
			)
		self.assertEqual(res.status_code, 302)
		n = Notification.objects.order_by("-id").first()
		self.assertIsNotNone(n)

		recipient_ids = list(
			NotificationRecipient.objects.filter(notification=n).values_list("teacher_id", flat=True).order_by("teacher_id")
		)
		self.assertEqual(recipient_ids, [self.t1.id])


class SubscriptionCancellationFinanceLogTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school-fin")
		self.plan = SubscriptionPlan.objects.create(name="Plan", price=100, days_duration=30, is_active=True)
		today = timezone.localdate()
		self.sub = SchoolSubscription.objects.create(
			school=self.school,
			plan=self.plan,
			start_date=today,
			end_date=today,
			is_active=True,
		)
		self.admin = Teacher.objects.create_superuser(phone="0577777777", name="Admin", password="pass")
		self.client.force_login(self.admin)

	def test_platform_subscription_delete_creates_cancelled_payment_event(self):
		# Pending payment for same period should be cancelled too
		pending = Payment.objects.create(
			school=self.school,
			subscription=self.sub,
			requested_plan=self.plan,
			amount=self.plan.price,
			receipt_image=None,
			payment_date=timezone.localdate(),
			status=Payment.Status.PENDING,
			notes="pending",
			created_by=self.admin,
		)

		url = reverse("reports:platform_subscription_delete", args=[self.sub.pk])
		res = self.client.post(url, {"reason": "cancel", "next": reverse("reports:platform_subscriptions_list")})
		self.assertEqual(res.status_code, 302)

		self.sub.refresh_from_db()
		self.assertFalse(self.sub.is_active)
		self.assertTrue(bool(self.sub.canceled_at))
		self.assertEqual((self.sub.cancel_reason or "").strip(), "cancel")

		# Event row exists
		self.assertTrue(
			Payment.objects.filter(
				subscription=self.sub,
				status=Payment.Status.CANCELLED,
				amount=0,
			).exists()
		)

		pending.refresh_from_db()
		self.assertEqual(pending.status, Payment.Status.CANCELLED)

	def test_django_admin_cancellation_creates_cancelled_payment_event(self):
		from .admin import SchoolSubscriptionAdmin

		# Prepare a pending payment to ensure it gets cancelled
		p = Payment.objects.create(
			school=self.school,
			subscription=self.sub,
			requested_plan=self.plan,
			amount=self.plan.price,
			receipt_image=None,
			payment_date=timezone.localdate(),
			status=Payment.Status.PENDING,
			notes="pending",
			created_by=self.admin,
		)

		# Simulate admin change: set cancellation fields
		self.sub.is_active = False
		self.sub.canceled_at = timezone.now()
		self.sub.cancel_reason = "admin cancel"

		admin_obj = SchoolSubscriptionAdmin(SchoolSubscription, AdminSite())
		rf = RequestFactory()
		req = rf.post("/admin-panel/reports/schoolsubscription/")
		req.user = self.admin
		admin_obj.save_model(request=req, obj=self.sub, form=None, change=True)

		self.sub.refresh_from_db()
		self.assertFalse(self.sub.is_active)
		self.assertTrue(bool(self.sub.canceled_at))
		self.assertEqual((self.sub.cancel_reason or "").strip(), "admin cancel")

		self.assertTrue(
			Payment.objects.filter(
				subscription=self.sub,
				status=Payment.Status.CANCELLED,
				amount=0,
			).exists()
		)

		p.refresh_from_db()


class PublicUserGuideTests(TestCase):
	def test_user_guide_page_is_public(self):
		url = reverse("reports:user_guide")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, "دليل المستخدم")

	def test_user_guide_download_is_public(self):
		url = reverse("reports:user_guide_download")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)

	def test_user_guide_download_pdf_is_public(self):
		url = reverse("reports:user_guide_download_pdf")
		res = self.client.get(url)
		self.assertIn(res.status_code, (200, 503))
		if res.status_code == 200:
			self.assertTrue(res.get("Content-Type", "").startswith("application/pdf"))
			self.assertIn("attachment", (res.get("Content-Disposition") or "").lower())
		else:
			self.assertContains(res, "PDF", status_code=503)


class ActiveSchoolGuardMiddlewareTests(TestCase):
	def setUp(self):
		self.school_a = School.objects.create(name="School A", code="asg-a", is_active=True)
		self.school_b = School.objects.create(name="School B", code="asg-b", is_active=True)

		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school_a, plan=plan, start_date=today, end_date=today)
		SchoolSubscription.objects.create(school=self.school_b, plan=plan, start_date=today, end_date=today)

		self.teacher = Teacher.objects.create_user(phone="0500000900", name="Teacher", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

		self.platform = Teacher.objects.create_user(
			phone="0500000901",
			name="Platform",
			password="pass",
			is_platform_admin=True,
		)
		scope = PlatformAdminScope.objects.create(admin=self.platform)
		scope.allowed_schools.add(self.school_a)

	def test_clears_active_school_when_not_in_user_memberships(self):
		self.client.force_login(self.teacher)
		session = self.client.session
		session["active_school_id"] = self.school_b.id
		session.save()

		res = self.client.get(reverse("reports:home"))
		self.assertEqual(res.status_code, 200)
		# Middleware clears the invalid school, and the view auto-selects the user's only school.
		self.assertEqual(self.client.session.get("active_school_id"), self.school_a.id)

	def test_keeps_active_school_when_in_user_memberships(self):
		self.client.force_login(self.teacher)
		session = self.client.session
		session["active_school_id"] = self.school_a.id
		session.save()

		res = self.client.get(reverse("reports:home"))
		self.assertEqual(res.status_code, 200)
		self.assertEqual(self.client.session.get("active_school_id"), self.school_a.id)

	def test_platform_admin_active_school_must_be_in_scope(self):
		self.client.force_login(self.platform)
		session = self.client.session
		session["active_school_id"] = self.school_b.id
		session.save()

		res = self.client.get(reverse("reports:platform_schools_directory"))
		self.assertEqual(res.status_code, 200)
		self.assertIsNone(self.client.session.get("active_school_id"))


class PaymentApprovalAppliesRequestedPlanTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school-pay")
		self.plan_a = SubscriptionPlan.objects.create(name="Plan A", price=100, days_duration=30, is_active=True)
		self.plan_b = SubscriptionPlan.objects.create(name="Plan B", price=200, days_duration=90, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=self.plan_a, start_date=today, end_date=today)

		self.admin = Teacher.objects.create_superuser(phone="0599999999", name="Admin", password="pass")
		self.client.force_login(self.admin)

	def test_approving_payment_does_not_change_subscription_plan(self):
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
		# تغيير الباقة تم إلغاؤه من النظام: يبقى الاشتراك على نفس الباقة.
		self.assertEqual(sub.plan_id, self.plan_a.id)


class PlatformSubscriptionAddRenewsCancelledTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school-cancel")
		self.plan_a = SubscriptionPlan.objects.create(name="Plan A", price=100, days_duration=30, is_active=True)
		self.plan_b = SubscriptionPlan.objects.create(name="Plan B", price=200, days_duration=90, is_active=True)

		today = timezone.localdate()
		self.sub = SchoolSubscription.objects.create(
			school=self.school,
			plan=self.plan_a,
			start_date=today,
			end_date=today,
			is_active=False,
			canceled_at=timezone.now(),
			cancel_reason="test",
		)

		self.admin = Teacher.objects.create_superuser(phone="0588888888", name="Admin", password="pass")
		self.client.force_login(self.admin)

	def test_add_subscription_renews_cancelled_instead_of_duplicate(self):
		url = reverse("reports:platform_subscription_add")
		res = self.client.post(
			url,
			{
				"school": self.school.id,
				"plan": self.plan_b.id,
				"is_active": "on",
			},
		)
		self.assertEqual(res.status_code, 302)

		self.sub.refresh_from_db()
		self.assertTrue(self.sub.is_active)
		self.assertIsNone(self.sub.canceled_at)
		self.assertEqual((self.sub.cancel_reason or "").strip(), "")
		# الباقة تتغير حسب اختيار الإدارة عند إضافة اشتراك جديد بعد الإلغاء
		self.assertEqual(self.sub.plan_id, self.plan_b.id)

		# تم تسجيل عملية مالية approved
		self.assertTrue(
			Payment.objects.filter(
				subscription=self.sub,
				status=Payment.Status.APPROVED,
				amount=self.plan_b.price,
				requested_plan=self.plan_b,
			).exists()
		)

	def test_renewal_records_finance_even_if_old_payment_has_same_payment_date(self):
		# Payment قديم (نفس payment_date=اليوم) لكن تم إنشاؤه قبل فترة.
		today = timezone.localdate()
		old = Payment.objects.create(
			school=self.school,
			subscription=self.sub,
			requested_plan=self.plan_b,
			amount=self.plan_b.price,
			receipt_image=None,
			payment_date=today,
			status=Payment.Status.APPROVED,
			notes="old",
			created_by=self.admin,
		)
		# نجعل created_at في الماضي لتُمثل دفعة لفترة سابقة
		Payment.objects.filter(pk=old.pk).update(created_at=timezone.now() - timezone.timedelta(days=10))

		url = reverse("reports:platform_subscription_add")
		res = self.client.post(
			url,
			{
				"school": self.school.id,
				"plan": self.plan_b.id,
				"is_active": "on",
			},
		)
		self.assertEqual(res.status_code, 302)

		# يجب تسجيل دفعة جديدة عند التجديد
		self.assertGreaterEqual(
			Payment.objects.filter(
				subscription=self.sub,
				status=Payment.Status.APPROVED,
				amount=self.plan_b.price,
				requested_plan=self.plan_b,
			).count(),
			2,
		)


class PlatformSubscriptionDetailViewTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School Detail", code="school-detail")
		self.plan = SubscriptionPlan.objects.create(name="Plan Detail", price=120, days_duration=30, is_active=True)
		today = timezone.localdate()
		self.sub = SchoolSubscription.objects.create(
			school=self.school,
			plan=self.plan,
			start_date=today,
			end_date=today,
			is_active=True,
		)

		self.admin = Teacher.objects.create_superuser(phone="0581234567", name="Admin Detail", password="pass")
		self.other_user = Teacher.objects.create_user(phone="0581234568", name="User Detail", password="pass")

		Payment.objects.create(
			school=self.school,
			subscription=self.sub,
			requested_plan=self.plan,
			amount=self.plan.price,
			receipt_image=None,
			payment_date=today,
			status=Payment.Status.APPROVED,
			created_by=self.admin,
		)

	def test_platform_subscription_detail_renders_for_superuser(self):
		self.client.force_login(self.admin)
		url = reverse("reports:platform_subscription_detail", args=[self.sub.pk])
		res = self.client.get(url)

		self.assertEqual(res.status_code, 200)
		self.assertContains(res, self.school.name)
		self.assertContains(res, self.plan.name)
		self.assertContains(res, "سجل العمليات المالية")

	def test_platform_subscription_detail_requires_superuser(self):
		self.client.force_login(self.other_user)
		url = reverse("reports:platform_subscription_detail", args=[self.sub.pk])
		res = self.client.get(url)

		self.assertEqual(res.status_code, 302)
		self.assertIn(reverse("reports:login"), res["Location"])


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


class DepartmentApiIsolationTests(TestCase):
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


class ReportViewerRouteRegressionTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="Viewer School", code="viewer-school")
		plan = SubscriptionPlan.objects.create(name="Viewer Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.teacher = Teacher.objects.create_user(phone="0500000301", name="Teacher", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		self.report = Report.objects.create(
			school=self.school,
			teacher=self.teacher,
			title="Viewer Visible Report",
			report_date=today,
			idea="Visible to report viewer",
		)

		self.viewer = Teacher.objects.create_user(phone="0500000302", name="Viewer", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.viewer,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)

	def _login_viewer(self):
		self.client.force_login(self.viewer)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

	def test_report_viewer_home_redirects_to_readonly_reports(self):
		self._login_viewer()
		res = self.client.get(reverse("reports:home"))
		self.assertEqual(res.status_code, 302)
		self.assertEqual(res["Location"], reverse("reports:school_reports_readonly"))

	def test_report_viewer_readonly_page_lists_school_reports(self):
		self._login_viewer()
		res = self.client.get(reverse("reports:school_reports_readonly"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, self.report.title)

	def test_report_viewer_can_print_school_report(self):
		self._login_viewer()
		res = self.client.get(reverse("reports:report_print", args=[self.report.pk]))
		self.assertEqual(res.status_code, 200)


class ReportViewerSourceOfTruthTests(TestCase):
	def setUp(self):
		self.school_a = School.objects.create(name="Viewer A", code="viewer-a")
		self.school_b = School.objects.create(name="Viewer B", code="viewer-b")
		plan = SubscriptionPlan.objects.create(name="Viewer Scope Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school_a, plan=plan, start_date=today, end_date=today)
		SchoolSubscription.objects.create(school=self.school_b, plan=plan, start_date=today, end_date=today)

		self.viewer = Teacher.objects.create_user(phone="0500000303", name="Scoped Viewer", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=self.viewer,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)

	def test_permissions_helper_scopes_report_viewer_membership(self):
		from .permissions import is_report_viewer_for_school

		self.assertTrue(is_report_viewer_for_school(self.viewer))
		self.assertTrue(is_report_viewer_for_school(self.viewer, self.school_a))
		self.assertFalse(is_report_viewer_for_school(self.viewer, self.school_b))
		self.assertFalse(is_report_viewer_for_school(self.viewer, active_school_id=self.school_b.id))

	@override_settings(NAV_CONTEXT_CACHE_TTL_SECONDS=0)
	def test_nav_context_uses_same_report_viewer_source(self):
		from .context_processors import nav_context

		request = RequestFactory().get("/")
		request.user = self.viewer
		request.session = {"active_school_id": self.school_a.id}

		ctx = nav_context(request)
		self.assertTrue(ctx["IS_REPORT_VIEWER"])


class RoleResolutionSourceOfTruthTests(TestCase):
	def setUp(self):
		from .models import Role

		self.school = School.objects.create(
			name="Girls School",
			code="girls-school",
			gender=School.Gender.GIRLS,
		)
		plan = SubscriptionPlan.objects.create(name="Role Resolution Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.teacher_role = Role.objects.create(slug="legacy-teacher", name="دور قديم")
		self.teacher = Teacher.objects.create_user(phone="0500000304", name="Teacher Label", password="pass")
		self.teacher.role = self.teacher_role
		self.teacher.save()
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			job_title=SchoolMembership.JobTitle.ADMIN_STAFF,
			is_active=True,
		)

		self.manager_role, _ = Role.objects.get_or_create(
			slug="manager",
			defaults={"name": "مدير قديم", "is_staff_by_default": True},
		)
		self.legacy_manager = Teacher.objects.create_user(phone="0500000305", name="Legacy Manager", password="pass")
		self.legacy_manager.role = self.manager_role
		self.legacy_manager.save()
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.legacy_manager,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

	def _request_for(self, user):
		request = RequestFactory().get("/")
		request.user = user
		request.session = {"active_school_id": self.school.id}
		request.COOKIES = {}
		return request

	@override_settings(NAV_CONTEXT_CACHE_TTL_SECONDS=0)
	def test_nav_context_and_teacher_property_use_same_membership_label(self):
		from .context_processors import nav_context

		request = self._request_for(self.teacher)
		ctx = nav_context(request)
		self.assertEqual(ctx["USER_ROLE_LABEL"], "موظفة إدارية")

		with patch("reports.middleware.get_current_request", return_value=request):
			self.assertEqual(self.teacher.display_role_label, "موظفة إدارية")

	def test_manager_helper_separates_strict_and_legacy_modes(self):
		from .permissions import effective_user_role_label, is_school_manager

		self.assertFalse(is_school_manager(self.legacy_manager, self.school))
		self.assertTrue(is_school_manager(self.legacy_manager, self.school, allow_legacy_role=True))
		self.assertEqual(
			effective_user_role_label(self.legacy_manager, active_school=self.school),
			"مديرة المدرسة",
		)


class MembershipBasedSchoolViewsRegressionTests(TestCase):
	def setUp(self):
		from .models import Role

		self.school = School.objects.create(name="Membership School", code="membership-school")
		plan = SubscriptionPlan.objects.create(name="Membership Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.manager = Teacher.objects.create_user(phone="0500000306", name="Manager Membership", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.legacy_department_role = Role.objects.create(slug="science-legacy", name="علوم")
		self.teacher = Teacher.objects.create_user(phone="0500000307", name="Legacy Science Teacher", password="pass")
		self.teacher.role = self.legacy_department_role
		self.teacher.save()
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			job_title=SchoolMembership.JobTitle.ADMIN_STAFF,
			is_active=True,
		)

		self.department = Department.objects.create(
			school=self.school,
			name="Science Department",
			slug="science-legacy",
			is_active=True,
		)
		self.member = Teacher.objects.create_user(phone="0500000308", name="Actual Science Member", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.member,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		DepartmentMembership.objects.create(department=self.department, teacher=self.member)

		self.client.force_login(self.manager)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

	def test_departments_count_comes_from_department_memberships_only(self):
		from .views.schools import _all_departments

		items = _all_departments(self.school)
		dept = next(item for item in items if item["slug"] == "science-legacy")
		self.assertEqual(dept["members_count"], 1)

	def test_manage_teachers_uses_membership_role_label_not_legacy_role_name(self):
		res = self.client.get(reverse("reports:manage_teachers"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, self.teacher.name)
		self.assertContains(res, "موظف إداري")
		self.assertNotContains(res, "علوم")


class SchoolManagerMembershipViewsRegressionTests(TestCase):
	def setUp(self):
		from .models import Role

		self.admin = Teacher.objects.create_superuser(phone="0500000309", name="Admin Root", password="pass")
		self.school_a = School.objects.create(name="Manager School A", code="manager-school-a")
		self.school_b = School.objects.create(name="Manager School B", code="manager-school-b")
		plan = SubscriptionPlan.objects.create(name="Manager Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school_a, plan=plan, start_date=today, end_date=today)
		SchoolSubscription.objects.create(school=self.school_b, plan=plan, start_date=today, end_date=today)

		manager_role, _ = Role.objects.get_or_create(
			slug="manager",
			defaults={"name": "مدير قديم", "is_staff_by_default": True},
		)
		self.role_only_manager = Teacher.objects.create_user(phone="0500000310", name="Legacy Role Only", password="pass")
		self.role_only_manager.role = manager_role
		self.role_only_manager.save()

		self.real_manager = Teacher.objects.create_user(phone="0500000312", name="Real Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=self.real_manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.other_manager = Teacher.objects.create_user(phone="0500000313", name="Other Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school_b,
			teacher=self.other_manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.client.force_login(self.admin)

	def test_school_managers_list_ignores_role_only_manager_accounts(self):
		res = self.client.get(reverse("reports:school_managers_list"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, self.real_manager.name)
		self.assertContains(res, self.other_manager.name)
		self.assertNotContains(res, self.role_only_manager.name)

	def test_school_managers_manage_candidates_come_from_manager_memberships(self):
		res = self.client.get(reverse("reports:school_managers_manage", args=[self.school_a.pk]))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, self.other_manager.name)
		self.assertNotContains(res, self.role_only_manager.name)


class TicketListSerializerRegressionTests(TestCase):
	def test_ticket_list_serializer_uses_only_model_fields(self):
		from .serializers import TicketListSerializer

		fields = TicketListSerializer().get_fields()
		self.assertIn("status", fields)
		self.assertNotIn("priority", fields)


class TeacherEditFormRegressionTests(TestCase):
	def test_save_updates_school_job_title_membership(self):
		from .forms import TeacherEditForm

		school = School.objects.create(name="Teacher Form School", code="teacher-form-school")
		plan = SubscriptionPlan.objects.create(name="Form Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=school, plan=plan, start_date=today, end_date=today)

		teacher = Teacher.objects.create_user(phone="0500000311", name="Teacher Form", password="pass")
		membership = SchoolMembership.objects.create(
			school=school,
			teacher=teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
			job_title=SchoolMembership.JobTitle.TEACHER,
		)

		form = TeacherEditForm(
			data={
				"name": "Teacher Form",
				"phone": "0500000311",
				"national_id": "",
				"is_active": "on",
				"job_title": SchoolMembership.JobTitle.ADMIN_STAFF,
				"password": "",
			},
			instance=teacher,
			active_school=school,
		)
		self.assertTrue(form.is_valid(), form.errors)
		form.save()

		membership.refresh_from_db()
		self.assertEqual(membership.job_title, SchoolMembership.JobTitle.ADMIN_STAFF)

	def test_job_title_choices_follow_school_gender(self):
		from .forms import TeacherCreateForm, TeacherEditForm

		girls_school = School.objects.create(
			name="Girls Teacher Form School",
			code="girls-teacher-form-school",
			gender=School.Gender.GIRLS,
		)
		boys_school = School.objects.create(
			name="Boys Teacher Form School",
			code="boys-teacher-form-school",
			gender=School.Gender.BOYS,
		)
		teacher = Teacher.objects.create_user(phone="0500000312", name="Edit Form Teacher", password="pass")

		girls_form = TeacherCreateForm(active_school=girls_school)
		self.assertEqual(
			list(girls_form.fields["job_title"].choices),
			[
				(SchoolMembership.JobTitle.TEACHER, "معلمة"),
				(SchoolMembership.JobTitle.ADMIN_STAFF, "موظفة إدارية"),
				(SchoolMembership.JobTitle.LAB_TECH, "محضرة مختبر"),
			],
		)

		boys_form = TeacherEditForm(instance=teacher, active_school=boys_school)
		self.assertEqual(
			list(boys_form.fields["job_title"].choices),
			[
				(SchoolMembership.JobTitle.TEACHER, "معلم"),
				(SchoolMembership.JobTitle.ADMIN_STAFF, "موظف إداري"),
				(SchoolMembership.JobTitle.LAB_TECH, "محضر مختبر"),
			],
		)


class AddTeacherGenderedLabelsViewTests(TestCase):
	def test_add_teacher_page_uses_feminine_job_titles_for_girls_school(self):
		girls_school = School.objects.create(
			name="Girls Add Teacher School",
			code="girls-add-teacher-school",
			gender=School.Gender.GIRLS,
		)
		plan = SubscriptionPlan.objects.create(name="Girls View Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=girls_school, plan=plan, start_date=today, end_date=today)

		manager = Teacher.objects.create_user(phone="0500000313", name="Girls Manager", password="pass")
		SchoolMembership.objects.create(
			school=girls_school,
			teacher=manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.client.force_login(manager)
		session = self.client.session
		session["active_school_id"] = girls_school.id
		session.save()

		res = self.client.get(reverse("reports:add_teacher"))
		self.assertEqual(res.status_code, 200)
		self.assertContains(res, '<option value="teacher">معلمة</option>', html=True)
		self.assertContains(res, '<option value="admin_staff">موظفة إدارية</option>', html=True)
		self.assertContains(res, '<option value="lab_tech">محضرة مختبر</option>', html=True)


class LegacyRoleCompatibilityServiceTests(TestCase):
	def test_legacy_role_write_surfaces_inventory_is_centralized(self):
		from .services_legacy_roles import LEGACY_ROLE_WRITE_SURFACES

		self.assertEqual(
			set(LEGACY_ROLE_WRITE_SURFACES),
			{
				"forms.TeacherForm",
				"forms.TeacherCreateForm",
				"forms.TeacherEditForm",
				"forms.PlatformAdminCreateForm",
				"views.achievements.report_viewer_create",
				"views.achievements.report_viewer_update",
			},
		)

	def test_teacher_create_form_creates_legacy_teacher_role_when_missing(self):
		from .forms import TeacherCreateForm
		from .models import Role

		Role.objects.filter(slug="teacher").delete()

		form = TeacherCreateForm(
			data={
				"name": "Legacy Create",
				"phone": "0500000314",
				"national_id": "",
				"is_active": "on",
				"job_title": SchoolMembership.JobTitle.TEACHER,
				"password": "pass12345",
			}
		)
		self.assertTrue(form.is_valid(), form.errors)

		teacher = form.save()
		teacher.refresh_from_db()

		self.assertEqual(getattr(teacher.role, "slug", None), "teacher")
		self.assertTrue(Role.objects.filter(slug="teacher").exists())

	def test_teacher_form_maps_department_to_existing_legacy_role_via_service(self):
		from .forms import TeacherForm
		from .models import Role

		school = School.objects.create(name="Legacy Role School", code="legacy-role-school")
		department = Department.objects.create(
			school=school,
			name="Science Office",
			slug="science-office",
			is_active=True,
		)
		legacy_role = Role.objects.create(slug="science-office", name="علوم قديم")

		form = TeacherForm(
			data={
				"name": "Department Legacy User",
				"phone": "0500000315",
				"national_id": "",
				"is_active": "on",
				"department": department.slug,
				"membership_role": DepartmentMembership.OFFICER,
				"password": "pass12345",
			},
			active_school=school,
		)
		self.assertTrue(form.is_valid(), form.errors)

		teacher = form.save()
		teacher.refresh_from_db()
		membership = DepartmentMembership.objects.get(department=department, teacher=teacher)

		self.assertEqual(teacher.role_id, legacy_role.id)
		self.assertEqual(membership.role_type, DepartmentMembership.OFFICER)


class TicketRecipientRegressionTests(TestCase):
	def test_attachment_helpers_proxy_parent_ticket_attachment(self):
		school = School.objects.create(name="Ticket School", code="ticket-school")
		teacher = Teacher.objects.create_user(phone="0500000321", name="Ticket User", password="pass")
		attachment = SimpleUploadedFile("evidence.PDF", b"dummy", content_type="application/pdf")
		ticket = Ticket.objects.create(
			school=school,
			creator=teacher,
			title="Attachment Ticket",
			body="Attachment body",
			attachment=attachment,
		)
		recipient = TicketRecipient.objects.create(ticket=ticket, teacher=teacher)

		self.assertTrue(recipient.attachment_is_pdf)
		self.assertTrue(recipient.attachment_name_lower.endswith(".pdf"))
		self.assertIn("evidence", recipient.attachment_name_lower)
		self.assertIn("response-content-disposition", recipient.attachment_download_url)


class ReportEditPermissionsTests(TestCase):
	def setUp(self):
		from .models import ReportType

		self.school = School.objects.create(name="School", code="school-edit")
		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.manager = Teacher.objects.create_user(phone="0500001001", name="Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.teacher = Teacher.objects.create_user(phone="0500001002", name="Teacher", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

		rt = ReportType.objects.create(name="Type A", code="type-a", is_active=True, school=self.school)
		self.report = Report.objects.create(
			school=self.school,
			teacher=self.teacher,
			title="R1",
			report_date=timezone.localdate(),
			category=rt,
		)

	def test_manager_can_open_edit_for_other_teachers_report_in_active_school(self):
		self.client.force_login(self.manager)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

		url = reverse("reports:edit_my_report", args=[self.report.pk])
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)


class StorageCompressionTests(TestCase):
	def test_compress_image_file_resizes_and_reduces_size(self):
		from io import BytesIO
		from PIL import Image
		from django.core.files.base import ContentFile
		from .storage import _compress_image_file

		# Create a deliberately large JPEG so resizing definitely happens.
		img = Image.new("RGB", (3000, 2000), (255, 0, 0))
		buf = BytesIO()
		img.save(buf, format="JPEG", quality=95)
		original_bytes = buf.getvalue()

		original = ContentFile(original_bytes)
		original.name = "big.jpg"

		compressed = _compress_image_file(original, max_size=1600, jpeg_quality=85)
		compressed_bytes = compressed.read()

		# Size should go down after resizing (3000px -> <=1600px)
		self.assertLess(len(compressed_bytes), len(original_bytes))

		# Dimensions should not exceed max_size
		out = Image.open(BytesIO(compressed_bytes))
		self.assertLessEqual(max(out.size), 1600)

	def test_compress_image_file_keeps_non_images_unchanged(self):
		from django.core.files.base import ContentFile
		from .storage import _compress_image_file

		data = b"not an image file"
		f = ContentFile(data)
		f.name = "x.txt"

		out = _compress_image_file(f)
		out_bytes = out.read()
		self.assertEqual(out_bytes, data)


class MySubscriptionViewTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="School", code="school-sub")
		self.manager = Teacher.objects.create_user(phone="0500000100", name="Manager", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)
		self.client.force_login(self.manager)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

	def test_my_subscription_renders_without_subscription(self):
		url = reverse("reports:my_subscription")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)

	def test_my_subscription_renders_with_subscription(self):
		plan = SubscriptionPlan.objects.create(name="Plan", price=10, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)
		url = reverse("reports:my_subscription")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)


class PlatformAdminApiScopeTests(TestCase):
	def setUp(self):
		self.school_a = School.objects.create(name="School A", code="pa-a")
		self.school_b = School.objects.create(name="School B", code="pa-b")

		plan = SubscriptionPlan.objects.create(name="Test", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school_a, plan=plan, start_date=today, end_date=today)
		SchoolSubscription.objects.create(school=self.school_b, plan=plan, start_date=today, end_date=today)

		self.dept_a = Department.objects.create(school=self.school_a, name="Dept A", slug="dept-a", is_active=True)
		self.dept_b = Department.objects.create(school=self.school_b, name="Dept B", slug="dept-b", is_active=True)
		self.global_dept = Department.objects.create(school=None, name="Global", slug="global", is_active=True)

		self.platform = Teacher.objects.create_user(
			phone="0500000099",
			name="Platform",
			password="pass",
			is_platform_admin=True,
		)
		scope = PlatformAdminScope.objects.create(admin=self.platform)
		scope.allowed_schools.add(self.school_a)
		self.client.force_login(self.platform)

	def test_api_school_departments_allows_platform_within_scope(self):
		url = reverse("reports:api_school_departments")
		res = self.client.get(url, {"school": self.school_a.id})
		self.assertEqual(res.status_code, 200)
		data = res.json()
		names = {row.get("name") for row in data.get("results", [])}
		self.assertIn("Dept A", names)
		self.assertIn("Global", names)
		self.assertNotIn("Dept B", names)

	def test_api_school_departments_forbids_platform_outside_scope(self):
		url = reverse("reports:api_school_departments")
		res = self.client.get(url, {"school": self.school_b.id})
		self.assertEqual(res.status_code, 403)

	def test_api_department_members_allows_platform_within_scope(self):
		member = Teacher.objects.create_user(phone="0500000101", name="Member", password="pass")
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=member,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)
		DepartmentMembership.objects.create(department=self.dept_a, teacher=member, role_type=DepartmentMembership.TEACHER)

		url = reverse("reports:api_department_members")
		res = self.client.get(url, {"department": self.dept_a.slug, "target_school": self.school_a.id})
		self.assertEqual(res.status_code, 200)
		data = res.json()
		ids = {row.get("id") for row in data.get("results", [])}
		self.assertIn(member.id, ids)

	def test_api_department_members_forbids_platform_outside_scope(self):
		url = reverse("reports:api_department_members")
		res = self.client.get(url, {"department": self.dept_a.slug, "target_school": self.school_b.id})
		self.assertEqual(res.status_code, 403)

# ================================================================
# Tenant Isolation Tests
# ================================================================
class TenantIsolationTests(TestCase):
	"""
	Verify that a teacher/manager in School A cannot access
	data from School B via the main views.
	"""

	def setUp(self):
		# --- Two schools with active subscriptions ---
		self.school_a = School.objects.create(name="School A", code="iso-a")
		self.school_b = School.objects.create(name="School B", code="iso-b")

		plan = SubscriptionPlan.objects.create(
			name="Basic", price=0, days_duration=365, is_active=True,
		)
		today = timezone.localdate()
		for s in (self.school_a, self.school_b):
			SchoolSubscription.objects.create(
				school=s, plan=plan,
				start_date=today,
				end_date=today + timezone.timedelta(days=365),
			)

		# --- Manager of school A ---
		self.manager_a = Teacher.objects.create_user(
			phone="0511100001", name="Manager A", password="pass",
		)
		SchoolMembership.objects.create(
			school=self.school_a,
			teacher=self.manager_a,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		# --- Teacher of school B ---
		self.teacher_b = Teacher.objects.create_user(
			phone="0511100002", name="Teacher B", password="pass",
		)
		SchoolMembership.objects.create(
			school=self.school_b,
			teacher=self.teacher_b,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

		# --- Report in school B ---
		self.report_b = Report.objects.create(
			school=self.school_b,
			teacher=self.teacher_b,
			title="Secret Report B",
			report_date=today,
			idea="Confidential data",
		)

		# --- Ticket in school B ---
		self.ticket_b = Ticket.objects.create(
			school=self.school_b,
			creator=self.teacher_b,
			title="Ticket B",
			body="Private issue",
		)

		# --- Notification in school B ---
		notif = Notification.objects.create(
			title="Notice B",
			message="For school B only",
			school=self.school_b,
			created_by=self.teacher_b,
		)
		self.notif_b = notif
		NotificationRecipient.objects.create(
			notification=notif,
			teacher=self.teacher_b,
		)

	def _login_as_manager_a(self):
		"""Log in as manager A and set active school to school A."""
		self.client.force_login(self.manager_a)
		session = self.client.session
		session["active_school_id"] = self.school_a.id
		session.save()

	# ── Reports ──────────────────────────────────────────────────
	def test_manager_a_cannot_see_school_b_reports_in_admin_list(self):
		self._login_as_manager_a()
		url = reverse("reports:admin_reports")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, "Secret Report B")

	def test_manager_a_cannot_delete_school_b_report(self):
		self._login_as_manager_a()
		url = reverse("reports:admin_delete_report", args=[self.report_b.pk])
		res = self.client.post(url)
		# Should be 404 or redirect, NOT 200 success
		self.assertIn(res.status_code, [302, 403, 404])
		self.assertTrue(Report.objects.filter(pk=self.report_b.pk).exists())

	# ── Tickets ──────────────────────────────────────────────────
	def test_manager_a_cannot_see_school_b_ticket(self):
		self._login_as_manager_a()
		url = reverse("reports:ticket_detail", args=[self.ticket_b.pk])
		res = self.client.get(url)
		self.assertIn(res.status_code, [302, 403, 404])

	def test_manager_a_cannot_see_school_b_tickets_in_inbox(self):
		self._login_as_manager_a()
		url = reverse("reports:tickets_inbox")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, "Ticket B")

	# ── Notifications ────────────────────────────────────────────
	def test_manager_a_cannot_see_school_b_notification(self):
		self._login_as_manager_a()
		url = reverse("reports:notification_detail", args=[self.notif_b.pk])
		res = self.client.get(url)
		self.assertIn(res.status_code, [302, 403, 404])

	# ── Dashboard ────────────────────────────────────────────────
	def test_manager_a_dashboard_shows_zero_for_school_b_data(self):
		"""Dashboard stats reflect only the active school, not cross-school."""
		self._login_as_manager_a()
		url = reverse("reports:admin_dashboard")
		res = self.client.get(url)
		self.assertEqual(res.status_code, 200)
		# School A has 0 reports, so the count should be 0
		self.assertContains(res, "0")


class DailyManagerReportTaskTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="مدرسة النور", code="daily-nour")
		plan = SubscriptionPlan.objects.create(name="Daily Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(
			school=self.school,
			plan=plan,
			start_date=today,
			end_date=today,
			is_active=True,
		)

		self.manager = Teacher.objects.create_user(
			phone="0500008800",
			name="مدير النور",
			password="pass",
			email="manager@nour.edu.sa",
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.teacher = Teacher.objects.create_user(phone="0500008801", name="معلم", password="pass")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

		Report.objects.create(
			school=self.school,
			teacher=self.teacher,
			title="تقرير يومي",
			report_date=timezone.localdate(),
			idea="تفاصيل",
		)
		Ticket.objects.create(
			school=self.school,
			creator=self.teacher,
			title="بلاغ مفتوح",
			body="open",
			status=Ticket.Status.OPEN,
		)
		Ticket.objects.create(
			school=self.school,
			creator=self.teacher,
			title="بلاغ مغلق",
			body="done",
			status=Ticket.Status.DONE,
		)

	@override_settings(
		DAILY_MANAGER_REPORT_ENABLED=True,
		DAILY_MANAGER_REPORT_EMAIL_ENABLED=True,
		DAILY_MANAGER_REPORT_WHATSAPP_ENABLED=False,
		SITE_URL="https://app.tawtheeq-ksa.com",
	)
	def test_daily_report_sends_email_to_manager(self):
		with patch("reports.tasks.send_mail", return_value=1) as mocked_send_mail:
			result = send_daily_manager_summary_task()

		self.assertEqual(result["schools_processed"], 1)
		self.assertEqual(result["emails_sent"], 1)
		self.assertEqual(result["email_failures"], 0)
		self.assertEqual(result["whatsapp_sent"], 0)
		self.assertEqual(result["whatsapp_failures"], 0)

		mocked_send_mail.assert_called_once()
		_, kwargs = mocked_send_mail.call_args
		self.assertIn("تقرير اليوم - مدرسة النور", kwargs["subject"])
		self.assertIn("عدد التقارير: 1", kwargs["message"])
		self.assertIn("البلاغات المفتوحة: 1", kwargs["message"])
		self.assertIn("البلاغات المغلقة: 1", kwargs["message"])
		self.assertIn(f"/staff/schools/{self.school.id}/profile/", kwargs["message"])
		self.assertEqual(kwargs["recipient_list"], ["manager@nour.edu.sa"])

	@override_settings(
		DAILY_MANAGER_REPORT_ENABLED=True,
		DAILY_MANAGER_REPORT_EMAIL_ENABLED=False,
		DAILY_MANAGER_REPORT_WHATSAPP_ENABLED=True,
		DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_URL="https://example.com/whatsapp-webhook",
	)
	def test_daily_report_sends_whatsapp_when_enabled(self):
		self.manager.email = ""
		self.manager.save(update_fields=["email"])

		with patch("reports.tasks._send_whatsapp_via_webhook", return_value=True) as mocked_whatsapp:
			result = send_daily_manager_summary_task()

		self.assertEqual(result["schools_processed"], 1)
		self.assertEqual(result["emails_sent"], 0)
		self.assertEqual(result["whatsapp_sent"], 1)
		self.assertEqual(result["whatsapp_failures"], 0)
		mocked_whatsapp.assert_called_once()

	@override_settings(
		DAILY_MANAGER_REPORT_ENABLED=True,
		DAILY_MANAGER_REPORT_INAPP_ENABLED=True,
		DAILY_MANAGER_REPORT_EMAIL_ENABLED=False,
		DAILY_MANAGER_REPORT_WHATSAPP_ENABLED=False,
	)
	def test_daily_report_creates_internal_notification_without_external_channels(self):
		result = send_daily_manager_summary_task()

		self.assertEqual(result["schools_processed"], 1)
		self.assertEqual(result["inapp_sent"], 1)
		self.assertEqual(result["inapp_failures"], 0)
		self.assertEqual(result["emails_sent"], 0)
		self.assertEqual(result["whatsapp_sent"], 0)
		self.assertEqual(result["managers_missing_channels"], 0)

		notification = Notification.objects.filter(school=self.school).order_by("-id").first()
		self.assertIsNotNone(notification)
		self.assertIn("تقرير اليوم", notification.title)
		self.assertIn("عدد التقارير: 1", notification.message)

		self.assertTrue(
			NotificationRecipient.objects.filter(
				notification=notification,
				teacher=self.manager,
			).exists()
		)


class LandingPricingDynamicTests(TestCase):
	def setUp(self):
		SubscriptionPlan.objects.create(
			name="تجربة مجانية",
			price=0,
			days_duration=14,
			max_teachers=5,
			description="تفعيل مباشر\nتجربة عملية للنظام",
			is_active=True,
		)
		SubscriptionPlan.objects.create(
			name="باقة 25 مستخدم",
			price=699,
			days_duration=180,
			max_teachers=25,
			description="مناسبة للمدارس الصغيرة\nتقارير وتذاكر وتعاميم\nدعم تشغيل",
			is_active=True,
		)
		SubscriptionPlan.objects.create(
			name="باقة 50 مستخدم - نصف سنوي",
			price=999,
			days_duration=180,
			max_teachers=50,
			description="الأكثر طلباً للتشغيل الكامل\nصلاحيات وأدوار متعددة\nمخرجات PDF رسمية",
			is_active=True,
		)
		SubscriptionPlan.objects.create(
			name="باقة 50 مستخدم - سنوي",
			price=999,
			days_duration=365,
			max_teachers=50,
			description="الأكثر طلباً للتشغيل الكامل\nصلاحيات وأدوار متعددة\nمخرجات PDF رسمية",
			is_active=True,
		)
		SubscriptionPlan.objects.create(
			name="باقة مخفية",
			price=1500,
			days_duration=365,
			max_teachers=80,
			description="يجب ألا تظهر",
			is_active=False,
		)

	def test_landing_pricing_uses_active_plans_only(self):
		res = self.client.get(reverse("reports:landing"))
		self.assertEqual(res.status_code, 200)

		trial_plan = res.context["pricing_trial_plan"]
		cards = res.context["pricing_cards"]
		names = [card["name"] for card in cards]

		self.assertEqual(trial_plan["name"], "التجربة المجانية")
		self.assertIn("باقة 25 مستخدم", names)
		self.assertIn("باقة 50 مستخدم", names)
		self.assertNotIn("باقة مخفية", names)

		self.assertContains(res, "14 يوم تجريبية")
		self.assertContains(res, 'data-period="6m"')
		self.assertContains(res, 'data-period="1y"')
		self.assertContains(res, "باقة 25 مستخدم")
		self.assertContains(res, "باقة 50 مستخدم")
		self.assertNotContains(res, "باقة مخفية")

	def test_landing_pricing_builds_advisor_context(self):
		res = self.client.get(reverse("reports:landing"))
		self.assertEqual(res.status_code, 200)

		recommended = res.context["pricing_recommended"]
		slider = res.context["pricing_slider"]
		marks = res.context["advisor_marks"]
		periods = res.context["pricing_periods"]

		self.assertIsNotNone(recommended)
		self.assertEqual(recommended["name"], "باقة 50 مستخدم")
		self.assertEqual(res.context["pricing_initial_period"], "6m")
		self.assertEqual(slider["min"], 5)
		self.assertGreaterEqual(slider["max"], 50)
		self.assertTrue(len(marks) >= 1)
		self.assertTrue(any(m["active"] for m in marks))
		self.assertTrue(any(period["key"] == "6m" and period["available"] for period in periods))
		self.assertTrue(any(period["key"] == "1y" and period["available"] for period in periods))


class SchoolRegistrationAutoCodeTests(TestCase):
	def _registration_payload(self, **overrides):
		payload = {
			"school_name": "Bind Test School",
			"stage": School.Stage.PRIMARY,
			"gender": School.Gender.BOYS,
			"city": "الرياض",
			"manager_name": "مدير تجريبي",
			"manager_phone": "0501234500",
			"password": "pass12345",
			"password_confirm": "pass12345",
		}
		payload.update(overrides)
		return payload

	def test_registration_page_hides_school_code_input(self):
		res = self.client.get(reverse("reports:register_school"))
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, 'name="school_code"')
		self.assertNotContains(res, 'id="id_school_code"')

	def test_registration_ignores_posted_school_code_and_generates_code(self):
		res = self.client.post(
			reverse("reports:register_school"),
			self._registration_payload(school_code="manual-override"),
		)
		self.assertEqual(res.status_code, 302)

		school = School.objects.get(name="Bind Test School")
		self.assertTrue(school.code)
		self.assertNotEqual(school.code, "manual-override")
		self.assertLessEqual(len(school.code), 64)
		self.assertNotIn(" ", school.code)


class ReportTypeAutoCodeTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="Report Type School", code="report-type-school")
		plan = SubscriptionPlan.objects.create(name="Report Type Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.manager = Teacher.objects.create_user(phone="0500000410", name="Report Type Manager", password="pass12345")
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.manager,
			role_type=SchoolMembership.RoleType.MANAGER,
			is_active=True,
		)

		self.client.force_login(self.manager)
		session = self.client.session
		session["active_school_id"] = self.school.id
		session.save()

	def test_reporttype_page_hides_code_input(self):
		res = self.client.get(reverse("reports:reporttype_create"))
		self.assertEqual(res.status_code, 200)
		self.assertNotContains(res, 'name="code"')
		self.assertNotContains(res, 'id="id_code"')
		self.assertContains(res, "سيتم إنشاء الرمز الداخلي تلقائيًا")

	def test_reporttype_create_ignores_posted_code_and_generates_it(self):
		res = self.client.post(
			reverse("reports:reporttype_create"),
			{
				"name": "Activity Report",
				"code": "manual-override",
				"description": "Auto code test",
				"order": "1",
				"is_active": "on",
			},
		)
		self.assertEqual(res.status_code, 302)

		report_type = ReportType.objects.get(school=self.school, name="Activity Report")
		self.assertTrue(report_type.code)
		self.assertEqual(report_type.code, "activity-report")
		self.assertNotEqual(report_type.code, "manual-override")
		self.assertNotIn(" ", report_type.code)


class SuperuserStaffRegressionTests(TestCase):
	def test_superuser_save_preserves_staff_flag(self):
		user = Teacher.objects.create_superuser(
			phone="0555000011",
			name="Root Admin",
			password="pass12345",
		)

		user.is_staff = False
		user.save()
		user.refresh_from_db()

		self.assertTrue(user.is_superuser)
		self.assertTrue(user.is_staff)


class ForcedPasswordChangeFlowTests(TestCase):
	def setUp(self):
		self.school = School.objects.create(name="Secure School", code="secure-school")
		plan = SubscriptionPlan.objects.create(name="Security Plan", price=0, days_duration=30, is_active=True)
		today = timezone.localdate()
		SchoolSubscription.objects.create(school=self.school, plan=plan, start_date=today, end_date=today)

		self.teacher_phone = "0501234567"
		self.teacher = Teacher.objects.create_user(
			phone=self.teacher_phone,
			name="Teacher Secure",
			password=self.teacher_phone,
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=self.teacher,
			role_type=SchoolMembership.RoleType.TEACHER,
			is_active=True,
		)

	def _login_with_default_password(self, phone):
		return self.client.post(
			reverse("reports:login"),
			{
				"phone": phone,
				"password": phone,
			},
		)

	def test_login_with_default_phone_password_redirects_to_profile(self):
		res = self._login_with_default_password(self.teacher_phone)

		self.assertEqual(res.status_code, 302)
		self.assertEqual(res["Location"], reverse("reports:my_profile"))
		self.assertTrue(bool(self.client.session.get(FORCE_PASSWORD_CHANGE_SESSION_KEY)))

		profile = self.client.get(reverse("reports:my_profile"))
		self.assertEqual(profile.status_code, 200)
		self.assertContains(profile, "خطوة سريعة لحماية حسابك")

	def test_forced_password_change_blocks_navigation_until_password_changes(self):
		self._login_with_default_password(self.teacher_phone)

		home = self.client.get(reverse("reports:home"), HTTP_ACCEPT="text/html")
		self.assertEqual(home.status_code, 302)
		self.assertEqual(home["Location"], reverse("reports:my_profile"))

		phone_update = self.client.post(
			reverse("reports:my_profile"),
			{
				"phone-phone": "0509999999",
				"update_phone": "1",
			},
		)
		self.assertEqual(phone_update.status_code, 302)
		self.assertEqual(phone_update["Location"], reverse("reports:my_profile"))

		new_password = "SafePass987!"
		password_update = self.client.post(
			reverse("reports:my_profile"),
			{
				"pwd-old_password": self.teacher_phone,
				"pwd-new_password1": new_password,
				"pwd-new_password2": new_password,
				"update_password": "1",
			},
		)
		self.assertEqual(password_update.status_code, 302)
		self.assertEqual(password_update["Location"], reverse("reports:my_profile"))

		self.teacher.refresh_from_db()
		self.assertTrue(self.teacher.check_password(new_password))
		self.assertFalse(bool(self.client.session.get(FORCE_PASSWORD_CHANGE_SESSION_KEY)))

		profile = self.client.get(reverse("reports:my_profile"))
		self.assertEqual(profile.status_code, 200)
		self.assertNotContains(profile, "خطوة سريعة لحماية حسابك")

	def test_report_viewer_can_open_profile_when_password_change_is_forced(self):
		viewer_phone = "0501234568"
		viewer = Teacher.objects.create_user(
			phone=viewer_phone,
			name="Viewer Secure",
			password=viewer_phone,
		)
		SchoolMembership.objects.create(
			school=self.school,
			teacher=viewer,
			role_type=SchoolMembership.RoleType.REPORT_VIEWER,
			is_active=True,
		)

		res = self._login_with_default_password(viewer_phone)
		self.assertEqual(res.status_code, 302)
		self.assertEqual(res["Location"], reverse("reports:my_profile"))

		profile = self.client.get(reverse("reports:my_profile"))
		self.assertEqual(profile.status_code, 200)
		self.assertContains(profile, "تغيير كلمة المرور الآن")
