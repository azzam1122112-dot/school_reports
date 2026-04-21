from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from reports.forms import NotificationCreateForm
from reports.models import (
    Department,
    DepartmentMembership,
    NotificationRecipient,
    Role,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CELERY_BROKER_URL="",
    NOTIFICATIONS_LOCAL_FALLBACK_ENABLED=True,
    NOTIFICATIONS_LOCAL_FALLBACK_THREAD=False,
    NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS=50,
)
class NotificationDispatchTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.manager_role, _ = Role.objects.get_or_create(
            slug="manager",
            defaults={
                "name": "Manager",
                "is_staff_by_default": True,
            },
        )
        self.teacher_role, _ = Role.objects.get_or_create(
            slug="teacher",
            defaults={"name": "Teacher"},
        )
        self.school = School.objects.create(name="Test School", code="test-school")
        plan = SubscriptionPlan.objects.create(
            name="Test Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.department = Department.objects.create(
            school=self.school,
            name="Science",
            slug="science",
            is_active=True,
        )
        self.manager = Teacher.objects.create_user(
            phone="500000001",
            name="School Manager",
            password="pass",
            role=self.manager_role,
            is_staff=True,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        self.teachers = []
        memberships = []
        for idx in range(3):
            teacher = Teacher.objects.create_user(
                phone=f"50000010{idx}",
                name=f"Teacher {idx}",
                password="pass",
                role=self.teacher_role,
            )
            memberships.append(
                SchoolMembership(
                    school=self.school,
                    teacher=teacher,
                    role_type=SchoolMembership.RoleType.TEACHER,
                )
            )
            self.teachers.append(teacher)
        SchoolMembership.objects.bulk_create(memberships)
        DepartmentMembership.objects.bulk_create(
            [
                DepartmentMembership(department=self.department, teacher=self.teachers[0]),
                DepartmentMembership(department=self.department, teacher=self.teachers[1]),
            ]
        )

    def _recipient_ids_for(self, notification):
        return set(
            NotificationRecipient.objects.filter(notification=notification)
            .values_list("teacher_id", flat=True)
        )

    def test_school_manager_notification_selected_teachers_dispatches_without_broker(self):
        form = NotificationCreateForm(
            data={
                "title": "Notification",
                "message": "Selected teachers only.",
                "teachers": [str(self.teachers[0].id), str(self.teachers[2].id)],
            },
            user=self.manager,
            active_school=self.school,
            mode="notification",
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        notification = form.save(creator=self.manager, default_school=self.school)

        self.assertEqual(
            self._recipient_ids_for(notification),
            {self.teachers[0].id, self.teachers[2].id},
        )

    def test_school_manager_notification_department_dispatches_without_broker(self):
        form = NotificationCreateForm(
            data={
                "title": "Department Notification",
                "message": "Department members.",
                "target_department": str(self.department.id),
            },
            user=self.manager,
            active_school=self.school,
            mode="notification",
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        notification = form.save(creator=self.manager, default_school=self.school)

        self.assertEqual(
            self._recipient_ids_for(notification),
            {self.teachers[0].id, self.teachers[1].id},
        )

    def test_school_manager_notification_department_without_active_members_is_invalid(self):
        empty_department = Department.objects.create(
            school=self.school,
            name="Empty Department",
            slug="empty-department",
            is_active=True,
        )
        form = NotificationCreateForm(
            data={
                "title": "Department Notification",
                "message": "No recipients in this department.",
                "target_department": str(empty_department.id),
            },
            user=self.manager,
            active_school=self.school,
            mode="notification",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("target_department", form.errors)
        self.assertIn("لا يحتوي على مستلمين نشطين", " ".join(form.errors.get("target_department", [])))

    def test_school_manager_circular_selected_teachers_dispatches_without_broker(self):
        form = NotificationCreateForm(
            data={
                "title": "Circular",
                "message": "Selected circular.",
                "teachers": [str(self.teachers[1].id)],
            },
            user=self.manager,
            active_school=self.school,
            mode="circular",
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        notification = form.save(
            creator=self.manager,
            default_school=self.school,
            force_requires_signature=True,
        )

        self.assertEqual(self._recipient_ids_for(notification), {self.teachers[1].id})

    def test_school_manager_circular_requires_explicit_recipients(self):
        form = NotificationCreateForm(
            data={
                "title": "Circular",
                "message": "Please read and sign.",
            },
            user=self.manager,
            active_school=self.school,
            mode="circular",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("teachers", form.errors)
        self.assertIn("المستلمون = 0", " ".join(form.errors.get("teachers", [])))

    def test_circular_create_view_selected_teacher_reaches_teacher_circulars_page(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.post(
            reverse("reports:circulars_create"),
            data={
                "title": "View Circular",
                "message": "Sent through the real create view.",
                "teachers": [str(self.teachers[0].id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            NotificationRecipient.objects.filter(
                teacher=self.teachers[0],
                notification__requires_signature=True,
                notification__title="View Circular",
            ).count(),
            1,
        )

        self.client.force_login(self.teachers[0])
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_circulars"))

        self.assertContains(response, "View Circular")

    @override_settings(CELERY_BROKER_URL="memory://")
    def test_circular_selected_teacher_creates_recipient_even_when_queued_without_worker(self):
        form = NotificationCreateForm(
            data={
                "title": "Queued Circular",
                "message": "Worker may be unavailable.",
                "teachers": [str(self.teachers[0].id)],
            },
            user=self.manager,
            active_school=self.school,
            mode="circular",
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        notification = form.save(
            creator=self.manager,
            default_school=self.school,
            force_requires_signature=True,
        )

        self.assertEqual(self._recipient_ids_for(notification), {self.teachers[0].id})
