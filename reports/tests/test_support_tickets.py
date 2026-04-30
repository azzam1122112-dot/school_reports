from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Role, School, SchoolMembership, SchoolSubscription, SubscriptionPlan, Teacher, Ticket


@override_settings(ALLOWED_HOSTS=["testserver"])
class MySupportTicketsViewTests(TestCase):
    def setUp(self):
        self.manager_role, _ = Role.objects.get_or_create(
            slug="manager",
            defaults={
                "name": "Manager",
                "is_staff_by_default": True,
            },
        )
        self.school = School.objects.create(name="Test School", code="test-school")
        plan = SubscriptionPlan.objects.create(
            name="Test Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
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

    def test_my_support_tickets_page_renders_with_search_query(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_support_tickets"), {"q": "network"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("reports:my_support_tickets"))

    def test_my_support_tickets_search_filters_results(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="انقطاع الشبكة",
            body="المعمل لا يصل إلى الإنترنت.",
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="مشكلة الطابعة",
            body="الطابعة تحتاج صيانة.",
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_support_tickets"), {"q": "الشبكة"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "انقطاع الشبكة")
        self.assertNotContains(response, "مشكلة الطابعة")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_status_filters_results(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة جديدة",
            body="بانتظار المعالجة.",
            status=Ticket.Status.OPEN,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة مكتملة",
            body="تمت المعالجة.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_support_tickets"), {"status": Ticket.Status.DONE})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تذكرة مكتملة")
        self.assertNotContains(response, "تذكرة جديدة")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_invalid_status_is_ignored(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة أولى",
            body="الوصف الأول.",
            status=Ticket.Status.OPEN,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة ثانية",
            body="الوصف الثاني.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_support_tickets"), {"status": "not-a-real-status"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تذكرة أولى")
        self.assertContains(response, "تذكرة ثانية")
        self.assertEqual(response.context["current_status"], "")

    def test_my_support_tickets_search_and_status_filters_are_combined(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الشبكة المدرسية - مكتمل",
            body="تم حل المشكلة بالكامل.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الشبكة المدرسية - جديد",
            body="المشكلة ما زالت قائمة.",
            status=Ticket.Status.OPEN,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الطابعة - مكتمل",
            body="تمت الصيانة.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "الشبكة", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الشبكة المدرسية - مكتمل")
        self.assertNotContains(response, "الشبكة المدرسية - جديد")
        self.assertNotContains(response, "الطابعة - مكتمل")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_filters_do_not_escape_active_school_scope(self):
        other_school = School.objects.create(name="Second School", code="second-school")
        plan = SubscriptionPlan.objects.create(
            name="Second Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=other_school, plan=plan)
        SchoolMembership.objects.create(
            school=other_school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الشبكة المركزية - المدرسة الحالية",
            body="مطابقة للنص والحالة داخل المدرسة النشطة.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=other_school,
            is_platform=True,
            title="الشبكة المركزية - مدرسة أخرى",
            body="مطابقة للنص والحالة لكن خارج المدرسة النشطة.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "الشبكة المركزية", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الشبكة المركزية - المدرسة الحالية")
        self.assertNotContains(response, "الشبكة المركزية - مدرسة أخرى")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_filters_do_not_escape_current_user_scope(self):
        other_manager = Teacher.objects.create_user(
            phone="500000099",
            name="Other User",
            password="pass",
            role=self.manager_role,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other_manager,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الشبكة الداخلية - تذكرتي",
            body="مطابقة للنص والحالة للمستخدم الحالي.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=other_manager,
            school=self.school,
            is_platform=True,
            title="الشبكة الداخلية - تذكرة مستخدم آخر",
            body="مطابقة للنص والحالة لكن تخص مستخدمًا آخر.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "الشبكة الداخلية", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الشبكة الداخلية - تذكرتي")
        self.assertNotContains(response, "الشبكة الداخلية - تذكرة مستخدم آخر")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_excludes_non_platform_tickets(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="الدعم المنصي - مطابق",
            body="مطابق للنص والحالة داخل الدعم المنصي.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=False,
            title="الدعم المنصي - غير منصي",
            body="مطابق للنص والحالة لكن خارج الدعم المنصي.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "الدعم المنصي", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الدعم المنصي - مطابق")
        self.assertNotContains(response, "الدعم المنصي - غير منصي")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_my_support_tickets_handles_empty_results_with_q_status(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة موجودة",
            body="لكنها لا تطابق الفلاتر المطلوبة.",
            status=Ticket.Status.OPEN,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "لا توجد مطابقة", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(len(response.context["page_obj"].object_list), 0)

    def test_my_support_tickets_out_of_range_page_with_q_status_returns_last_page(self):
        for idx in range(21):
            Ticket.objects.create(
                creator=self.manager,
                school=self.school,
                is_platform=True,
                title=f"تذكرة الصفحة {idx}",
                body="كلها تطابق نفس الفلاتر.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "تذكرة الصفحة", "status": Ticket.Status.DONE, "page": "999"},
        )

        page_obj = response.context["page_obj"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(page_obj.paginator.count, 21)
        self.assertEqual(page_obj.paginator.num_pages, 2)
        self.assertEqual(page_obj.number, 2)
        self.assertEqual(len(page_obj.object_list), 1)

    def test_my_support_tickets_non_numeric_page_with_q_status_returns_first_page(self):
        for idx in range(21):
            Ticket.objects.create(
                creator=self.manager,
                school=self.school,
                is_platform=True,
                title=f"تذكرة غير رقمية {idx}",
                body="كلها تطابق نفس الفلاتر.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "تذكرة غير رقمية", "status": Ticket.Status.DONE, "page": "abc"},
        )

        page_obj = response.context["page_obj"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(page_obj.paginator.count, 21)
        self.assertEqual(page_obj.paginator.num_pages, 2)
        self.assertEqual(page_obj.number, 1)
        self.assertEqual(len(page_obj.object_list), 20)

    def test_my_support_tickets_pagination_links_keep_q_and_status(self):
        for idx in range(21):
            Ticket.objects.create(
                creator=self.manager,
                school=self.school,
                is_platform=True,
                title=f"pager-test {idx}",
                body="كلها تطابق نفس الفلاتر.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "pager-test", "status": Ticket.Status.DONE, "page": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '?page=2&q=pager-test&status=done')

    def test_my_support_tickets_pagination_links_urlencode_arabic_q(self):
        search_text = "الدعم الفني & داخلي"
        for idx in range(21):
            Ticket.objects.create(
                creator=self.manager,
                school=self.school,
                is_platform=True,
                title=f"{search_text} {idx}",
                body="كلها تطابق نفس الفلاتر.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": search_text, "status": Ticket.Status.DONE, "page": "1"},
        )

        encoded_q = quote(search_text)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'?page=2&q={encoded_q}&status=done')

    def test_my_support_tickets_clear_link_resets_all_filters(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="فلتر قابل للمسح",
            body="تذكرة لاختبار زر المسح.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:my_support_tickets"),
            {"q": "فلتر", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("reports:my_support_tickets")}"')

    def test_my_support_tickets_clear_link_hidden_without_filters(self):
        Ticket.objects.create(
            creator=self.manager,
            school=self.school,
            is_platform=True,
            title="تذكرة بدون فلاتر",
            body="لاختبار عدم ظهور زر المسح.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:my_support_tickets"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>مسح<', html=False)
