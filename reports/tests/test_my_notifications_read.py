from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class MyNotificationsReadBehaviorTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="notif-read-school")
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.user = Teacher.objects.create_user(
            phone="500909090", name="معلم", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.n = Notification.objects.create(
            title="إشعار", message="نص", requires_signature=False, school=self.school
        )
        self.rec = NotificationRecipient.objects.create(
            notification=self.n, teacher=self.user
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_opening_list_does_not_mark_read(self):
        resp = self.client.get(reverse("reports:my_notifications"))
        self.assertEqual(resp.status_code, 200)
        self.rec.refresh_from_db()
        self.assertFalse(self.rec.is_read)  # يبقى غير مقروء بعد فتح القائمة

    def test_opening_detail_marks_read(self):
        self.client.get(
            reverse("reports:my_notification_detail", args=[self.rec.pk])
        )
        self.rec.refresh_from_db()
        self.assertTrue(self.rec.is_read)  # يُعلَّم مقروءًا عند فتح التفاصيل

    def test_mark_one_endpoint_marks_read(self):
        self.client.post(
            reverse("reports:notification_mark_read", args=[self.rec.pk])
        )
        self.rec.refresh_from_db()
        self.assertTrue(self.rec.is_read)
