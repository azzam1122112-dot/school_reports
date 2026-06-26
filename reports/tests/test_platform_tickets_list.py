from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import School, Ticket, Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlatformTicketsListTests(TestCase):
    def setUp(self):
        self.admin = Teacher.objects.create_superuser(
            phone="599111222", name="Admin", password="pass"
        )
        self.school = School.objects.create(name="مدرسة", code="pt-school")
        self.creator = Teacher.objects.create_user(
            phone="500333444", name="مدير", password="pass"
        )
        # تذاكر دعم منصّة بحالات مختلفة
        Ticket.objects.create(creator=self.creator, school=self.school, is_platform=True,
                              title="A", status=Ticket.Status.OPEN)
        Ticket.objects.create(creator=self.creator, school=self.school, is_platform=True,
                              title="B", status=Ticket.Status.OPEN)
        Ticket.objects.create(creator=self.creator, school=self.school, is_platform=True,
                              title="C", status=Ticket.Status.DONE)
        # تذكرة غير منصّة يجب ألا تُحتسب
        Ticket.objects.create(creator=self.creator, school=self.school, is_platform=False,
                              title="X", status=Ticket.Status.OPEN)
        self.client.force_login(self.admin)

    def test_tab_counts_reflect_platform_tickets_only(self):
        resp = self.client.get(reverse("reports:platform_tickets_list"))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context["tab_counts"]
        self.assertEqual(ctx["all"], 3)   # المنصّة فقط
        self.assertEqual(ctx["open"], 2)
        self.assertEqual(ctx["done"], 1)

    def test_status_filter_open(self):
        resp = self.client.get(reverse("reports:platform_tickets_list"), {"status": "open"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["tickets"]), 2)
        # العدّادات تبقى ثابتة عند الفلترة
        self.assertEqual(resp.context["tab_counts"]["all"], 3)
