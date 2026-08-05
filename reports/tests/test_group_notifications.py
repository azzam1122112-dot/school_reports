"""تعاميم المدير التنفيذي على مدارس مجموعته.

حدّ الصلاحية الذي تثبّته هذه الاختبارات: تواصل على مستوى المجموعة، بلا أي
سلطة على البيانات التشغيلية لأي مدرسة. فالاختبار الحارس ``is_school_manager``
يجب أن يبقى كاذباً بعد إرسال التعاميم كلها.
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    GroupNotificationBatch,
    Notification,
    NotificationRecipient,
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import (
    can_send_group_notification,
    can_view_group_batch,
    is_school_manager,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


@override_settings(ALLOWED_HOSTS=["testserver"])
class GroupNotificationTests(TestCase):
    def setUp(self):
        self.group = SchoolGroup.objects.create(name="مجمع النور", code="al-noor")
        self.rival_group = SchoolGroup.objects.create(name="مجمع الفجر", code="al-fajr")

        plan = SubscriptionPlan.objects.create(
            name="باقة", price=0, days_duration=365, max_teachers=0
        )
        self.schools = []
        for index in range(1, 4):
            school = School.objects.create(
                name=f"مدرسة {index}", code=f"school-{index}", group=self.group
            )
            SchoolSubscription.objects.create(school=school, plan=plan)
            self.schools.append(school)

        self.outside = School.objects.create(name="مدرسة خارجية", code="outside")
        SchoolSubscription.objects.create(school=self.outside, plan=plan)

        self.director = _user("مدير تنفيذي", "0500000001")
        SchoolGroupMembership.objects.create(group=self.group, user=self.director)

        # مدير ومعلم في كل مدرسة، لاختبار فئتَي المستقبلين.
        self.principals, self.teachers = [], []
        for index, school in enumerate(self.schools):
            principal = _user(f"مدير {index}", f"05001000{index:02d}")
            SchoolMembership.objects.create(
                school=school, teacher=principal, role_type=SchoolMembership.RoleType.MANAGER
            )
            self.principals.append(principal)

            teacher = _user(f"معلم {index}", f"05002000{index:02d}")
            SchoolMembership.objects.create(
                school=school, teacher=teacher, role_type=SchoolMembership.RoleType.TEACHER
            )
            self.teachers.append(teacher)

    def _send(self, schools=None, **overrides):
        payload = {
            "schools": [s.pk for s in (schools if schools is not None else self.schools)],
            "audience": "managers",
            "title": "تعميم المتابعة",
            "message": "يرجى رفع خطة التحسين.",
        }
        payload.update(overrides)
        return self.client.post(reverse("reports:group_notification_create"), payload)

    # ------------------------------------------------------------ الصلاحيات

    def test_sending_never_grants_school_management(self):
        """الخاصية الحرجة: التواصل لا يتحول إلى سلطة على المدرسة."""
        self.client.force_login(self.director)
        self._send()

        for school in self.schools:
            self.assertFalse(is_school_manager(self.director, active_school=school))
        self.assertFalse(is_school_manager(self.director))

    def test_a_school_outside_the_group_rejects_the_whole_send(self):
        """تنفيذ جزئي يوهم المرسِل أن التعميم وصل حيث لم يصل."""
        self.assertFalse(
            can_send_group_notification(self.director, [self.schools[0].pk, self.outside.pk])
        )

        self.client.force_login(self.director)
        response = self._send(schools=[self.schools[0], self.outside])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(GroupNotificationBatch.objects.count(), 0)
        # المشروع يُنشئ إشعارات أخرى عند تهيئة المدارس، فالعدّ مقصور على
        # إشعارات الدفعات حتى لا يخفي ضجيجُ التهيئة ما نقيسه.
        self.assertEqual(Notification.objects.filter(batch__isnull=False).count(), 0)

    def test_non_directors_cannot_reach_the_compose_screen(self):
        for user in (self.teachers[0], self.principals[0]):
            self.client.force_login(user)
            response = self.client.get(reverse("reports:group_notification_create"))
            self.assertEqual(response.status_code, 404)

    def test_report_is_visible_only_to_its_sender(self):
        self.client.force_login(self.director)
        self._send()
        batch = GroupNotificationBatch.objects.get()

        rival = _user("مدير تنفيذي آخر", "0500000009")
        SchoolGroupMembership.objects.create(group=self.rival_group, user=rival)
        self.assertFalse(can_view_group_batch(rival, batch))

        self.client.force_login(rival)
        response = self.client.get(
            reverse("reports:group_notification_report", args=[batch.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_losing_the_post_revokes_access_to_past_reports(self):
        self.client.force_login(self.director)
        self._send()
        batch = GroupNotificationBatch.objects.get()

        SchoolGroupMembership.objects.filter(user=self.director).update(is_active=False)
        fresh = Teacher.objects.get(pk=self.director.pk)
        self.assertFalse(can_view_group_batch(fresh, batch))

    # -------------------------------------------------------------- التسليم

    def test_managers_audience_reaches_principals_only(self):
        self.client.force_login(self.director)
        self._send(audience="managers")

        recipients = set(
            NotificationRecipient.objects.filter(
                notification__batch__isnull=False
            ).values_list("teacher_id", flat=True)
        )
        self.assertEqual(recipients, {p.pk for p in self.principals})
        for teacher in self.teachers:
            self.assertNotIn(teacher.pk, recipients)

    def test_all_audience_reaches_principals_and_teachers(self):
        self.client.force_login(self.director)
        self._send(audience="all")

        recipients = set(
            NotificationRecipient.objects.filter(
                notification__batch__isnull=False
            ).values_list("teacher_id", flat=True)
        )
        expected = {p.pk for p in self.principals} | {t.pk for t in self.teachers}
        self.assertEqual(recipients, expected)

    def test_each_school_receives_its_own_notification(self):
        """التفريع هو ما يُبقي شاشة مدير المدرسة تعمل بلا تعديل."""
        self.client.force_login(self.director)
        self._send()

        batch = GroupNotificationBatch.objects.get()
        notifications = Notification.objects.filter(batch=batch)
        self.assertEqual(notifications.count(), len(self.schools))
        self.assertEqual(
            set(notifications.values_list("school_id", flat=True)),
            {s.pk for s in self.schools},
        )
        self.assertEqual(set(batch.target_schools.values_list("id", flat=True)),
                         {s.pk for s in self.schools})

    def test_a_subset_of_schools_can_be_targeted(self):
        self.client.force_login(self.director)
        self._send(schools=self.schools[:2])

        sent = Notification.objects.filter(batch__isnull=False)
        self.assertEqual(sent.count(), 2)
        self.assertNotIn(
            self.schools[2].pk,
            set(sent.values_list("school_id", flat=True)),
        )

    # ------------------------------------------------------------- التوقيع

    def test_signature_flags_carry_through_to_every_school(self):
        deadline = timezone.now() + timezone.timedelta(days=5)
        self.client.force_login(self.director)
        self._send(
            requires_signature="on",
            signature_deadline_at=deadline.strftime("%Y-%m-%dT%H:%M"),
        )

        batch = GroupNotificationBatch.objects.get()
        self.assertTrue(batch.requires_signature)
        for notification in Notification.objects.filter(batch=batch):
            self.assertTrue(notification.requires_signature)
            self.assertIsNotNone(notification.signature_deadline_at)
            self.assertTrue(notification.signature_ack_text)

    def test_a_past_signature_deadline_is_rejected(self):
        past = timezone.now() - timezone.timedelta(days=1)
        self.client.force_login(self.director)
        response = self._send(
            requires_signature="on",
            signature_deadline_at=past.strftime("%Y-%m-%dT%H:%M"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(GroupNotificationBatch.objects.count(), 0)

    def test_deadline_is_dropped_when_no_signature_is_required(self):
        future = timezone.now() + timezone.timedelta(days=5)
        self.client.force_login(self.director)
        self._send(signature_deadline_at=future.strftime("%Y-%m-%dT%H:%M"))

        sent = Notification.objects.filter(batch__isnull=False)
        self.assertEqual(sent.count(), len(self.schools))
        for notification in sent:
            self.assertFalse(notification.requires_signature)
            self.assertIsNone(notification.signature_deadline_at)

    # -------------------------------------------------------------- التقرير

    def test_report_shows_who_read_and_who_did_not_with_school_names(self):
        self.client.force_login(self.director)
        self._send(audience="all")
        batch = GroupNotificationBatch.objects.get()

        read_by = self.teachers[0]
        NotificationRecipient.objects.filter(
            teacher=read_by, notification__batch=batch
        ).update(is_read=True, read_at=timezone.now())

        response = self.client.get(
            reverse("reports:group_notification_report", args=[batch.pk])
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["totals"]["read"], 1)
        self.assertEqual(
            response.context["totals"]["unread"],
            response.context["totals"]["total"] - 1,
        )
        for school in self.schools:
            self.assertContains(response, school.name)
        self.assertContains(response, read_by.name)
        self.assertContains(response, "لم يطّلع")

    def test_report_puts_the_least_read_school_first(self):
        self.client.force_login(self.director)
        self._send(audience="all")
        batch = GroupNotificationBatch.objects.get()

        busy = Notification.objects.get(batch=batch, school=self.schools[1])
        NotificationRecipient.objects.filter(notification=busy).update(
            is_read=True, read_at=timezone.now()
        )

        response = self.client.get(
            reverse("reports:group_notification_report", args=[batch.pk])
        )
        rows = response.context["rows"]
        self.assertEqual(rows[0]["read_percent"], 0)
        self.assertEqual(rows[-1]["school"].pk, self.schools[1].pk)
        self.assertEqual(rows[-1]["read_percent"], 100)

    def test_the_report_labels_principals_apart_from_teachers(self):
        self.client.force_login(self.director)
        self._send(audience="all")
        batch = GroupNotificationBatch.objects.get()

        response = self.client.get(
            reverse("reports:group_notification_report", args=[batch.pk])
        )
        people = {
            person["name"]: person
            for row in response.context["rows"]
            for person in row["people"]
        }
        self.assertTrue(people[self.principals[0].name]["is_manager"])
        self.assertFalse(people[self.teachers[0].name]["is_manager"])
