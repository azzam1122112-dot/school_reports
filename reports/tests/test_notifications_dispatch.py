from django.test import TransactionTestCase, override_settings
from django.core.cache import cache

from reports.forms import NotificationCreateForm
from reports.models import (
    Department,
    DepartmentMembership,
    NotificationRecipient,
    Role,
    School,
    SchoolMembership,
    Teacher,
)


@override_settings(
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

    def test_school_manager_circular_all_teachers_dispatches_without_broker(self):
        form = NotificationCreateForm(
            data={
                "title": "Circular",
                "message": "Please read and sign.",
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

        self.assertEqual(
            self._recipient_ids_for(notification),
            {teacher.id for teacher in self.teachers},
        )
