from urllib.parse import quote

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    Department,
    DepartmentMembership,
    Role,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
    Ticket,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class AssignedToMeViewTests(TestCase):
    def setUp(self):
        self.teacher_role, _ = Role.objects.get_or_create(
            slug="teacher",
            defaults={"name": "Teacher"},
        )
        self.school = School.objects.create(name="Assigned School", code="assigned-school")
        plan = SubscriptionPlan.objects.create(
            name="Assigned Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.user = Teacher.objects.create_user(
            phone="500000301",
            name="Assigned Teacher",
            password="pass",
            role=self.teacher_role,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def test_assigned_to_me_filters_q_status_and_excludes_platform_tickets(self):
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="التذكرة المسندة - مكتملة",
            body="هذه التذكرة يجب أن تظهر في assigned_to_me.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="التذكرة المسندة - جديدة",
            body="هذه التذكرة تطابق q فقط لكن يجب استبعادها بالحالة.",
            status=Ticket.Status.OPEN,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=True,
            title="التذكرة المسندة - دعم منصي",
            body="هذه التذكرة تطابق q والحالة لكنها يجب أن تُستبعد.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "التذكرة المسندة", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "التذكرة المسندة - مكتملة")
        self.assertNotContains(response, "التذكرة المسندة - جديدة")
        self.assertNotContains(response, "التذكرة المسندة - دعم منصي")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_assigned_to_me_filters_do_not_escape_active_school_scope(self):
        other_school = School.objects.create(name="Other Assigned School", code="other-assigned-school")
        plan = SubscriptionPlan.objects.create(
            name="Other Assigned Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=other_school, plan=plan)
        SchoolMembership.objects.create(
            school=other_school,
            teacher=self.user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="المسندة المشتركة - المدرسة الحالية",
            body="يجب أن تظهر داخل المدرسة النشطة.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=other_school,
            is_platform=False,
            title="المسندة المشتركة - مدرسة أخرى",
            body="تطابق نفس q وstatus لكنها خارج المدرسة النشطة.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "المسندة المشتركة", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "المسندة المشتركة - المدرسة الحالية")
        self.assertNotContains(response, "المسندة المشتركة - مدرسة أخرى")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_assigned_to_me_excludes_other_users_tickets_without_assignment_access(self):
        other_user = Teacher.objects.create_user(
            phone="500000302",
            name="Other Assigned User",
            password="pass",
            role=self.teacher_role,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other_user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        Ticket.objects.create(
            creator=other_user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="حدود الظهور - مسندة لي",
            body="هذه التذكرة يجب أن تظهر لأنها مسندة للمستخدم الحالي.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=other_user,
            assignee=other_user,
            school=self.school,
            is_platform=False,
            title="حدود الظهور - لا تخصني",
            body="هذه التذكرة تطابق q وstatus لكنها ليست مسندة لي ولا ضمن recipients.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "حدود الظهور", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "حدود الظهور - مسندة لي")
        self.assertNotContains(response, "حدود الظهور - لا تخصني")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_assigned_to_me_includes_tickets_when_user_is_in_recipients(self):
        other_user = Teacher.objects.create_user(
            phone="500000303",
            name="Recipient Owner",
            password="pass",
            role=self.teacher_role,
        )
        third_user = Teacher.objects.create_user(
            phone="500000304",
            name="Recipient Other",
            password="pass",
            role=self.teacher_role,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other_user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=third_user,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        visible_ticket = Ticket.objects.create(
            creator=other_user,
            assignee=other_user,
            school=self.school,
            is_platform=False,
            title="المستلمون - أظهرني",
            body="هذه التذكرة يجب أن تظهر لأن المستخدم الحالي ضمن recipients.",
            status=Ticket.Status.DONE,
        )
        visible_ticket.recipients.add(self.user)

        Ticket.objects.create(
            creator=other_user,
            assignee=third_user,
            school=self.school,
            is_platform=False,
            title="المستلمون - لا أظهر",
            body="هذه التذكرة مشابهة لكنها لا تحتوي المستخدم الحالي ضمن recipients.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "المستلمون", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "المستلمون - أظهرني")
        self.assertNotContains(response, "المستلمون - لا أظهر")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_assigned_to_me_includes_unassigned_department_tickets_for_department_member(self):
        user_department = Department.objects.create(
            school=self.school,
            name="قسم التقنية",
            slug="tech-dept",
        )
        other_department = Department.objects.create(
            school=self.school,
            name="قسم الإدارة",
            slug="admin-dept",
        )
        DepartmentMembership.objects.create(
            department=user_department,
            teacher=self.user,
            role_type=DepartmentMembership.TEACHER,
        )

        Ticket.objects.create(
            creator=self.user,
            assignee=None,
            department=user_department,
            school=self.school,
            is_platform=False,
            title="تذكرة القسم - تخص قسمي",
            body="هذه التذكرة غير مسندة ويجب أن تظهر لعضو القسم.",
            status=Ticket.Status.DONE,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=None,
            department=other_department,
            school=self.school,
            is_platform=False,
            title="تذكرة القسم - قسم آخر",
            body="هذه التذكرة غير مسندة لكنها لقسم آخر ويجب ألا تظهر.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "تذكرة القسم", "status": Ticket.Status.DONE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تذكرة القسم - تخص قسمي")
        self.assertNotContains(response, "تذكرة القسم - قسم آخر")
        self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_assigned_to_me_invalid_status_is_ignored_without_breaking_page(self):
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="الحالة غير الصالحة - مفتوحة",
            body="يجب أن تبقى ظاهرة مع status غير صالح.",
            status=Ticket.Status.OPEN,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="الحالة غير الصالحة - مكتملة",
            body="يجب أن تبقى ظاهرة أيضًا مع status غير صالح.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "الحالة غير الصالحة", "status": "bad-status"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الحالة غير الصالحة - مفتوحة")
        self.assertContains(response, "الحالة غير الصالحة - مكتملة")
        self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_assigned_to_me_empty_status_is_ignored_without_breaking_page(self):
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="الحالة الفارغة - قيد المعالجة",
            body="يجب أن تبقى ظاهرة مع status فارغ.",
            status=Ticket.Status.IN_PROGRESS,
        )
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="الحالة الفارغة - مرفوضة",
            body="يجب أن تبقى ظاهرة أيضًا مع status فارغ.",
            status=Ticket.Status.REJECTED,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {"q": "الحالة الفارغة", "status": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الحالة الفارغة - قيد المعالجة")
        self.assertContains(response, "الحالة الفارغة - مرفوضة")
        self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_assigned_to_me_non_numeric_page_returns_first_page_stably(self):
        for index in range(13):
            Ticket.objects.create(
                creator=self.user,
                assignee=self.user,
                school=self.school,
                is_platform=False,
                title=f"ترقيم المسندة - مطابق {index}",
                body="تذكرة مطابقة لاختبار paginator مع page غير رقمية.",
                status=Ticket.Status.DONE,
            )

        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="ترقيم المسندة - حالة مختلفة",
            body="يجب استبعادها لأن الحالة مختلفة.",
            status=Ticket.Status.OPEN,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {
                "q": "ترقيم المسندة",
                "status": Ticket.Status.DONE,
                "order": "created_at",
                "page": "abc",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 1)
        self.assertEqual(response.context["page_obj"].paginator.count, 13)

    def test_assigned_to_me_too_high_page_returns_last_page_stably(self):
        for index in range(13):
            Ticket.objects.create(
                creator=self.user,
                assignee=self.user,
                school=self.school,
                is_platform=False,
                title=f"آخر صفحة المسندة - مطابق {index}",
                body="تذكرة مطابقة لاختبار paginator مع صفحة أعلى من آخر صفحة.",
                status=Ticket.Status.DONE,
            )

        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="آخر صفحة المسندة - حالة مختلفة",
            body="يجب استبعادها لأن الحالة مختلفة.",
            status=Ticket.Status.REJECTED,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {
                "q": "آخر صفحة المسندة",
                "status": Ticket.Status.DONE,
                "order": "created_at",
                "page": 999,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(response.context["page_obj"].paginator.count, 13)

    def test_assigned_to_me_pagination_links_keep_q_status_order_and_view(self):
        for index in range(13):
            Ticket.objects.create(
                creator=self.user,
                assignee=self.user,
                school=self.school,
                is_platform=False,
                title=f"pager-assigned {index}",
                body="Assigned ticket for pagination link preservation.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {
                "q": "pager-assigned",
                "status": Ticket.Status.DONE,
                "order": "created_at",
                "view": "table",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].number, 1)
        self.assertContains(
            response,
            "?page=2&view=table&q=pager-assigned&status=done&order=created_at",
        )

    def test_assigned_to_me_links_urlencode_arabic_q_in_view_and_pagination(self):
        raw_q = "طلب عربي & خاص"
        encoded_q = quote(raw_q, safe="")

        for index in range(13):
            Ticket.objects.create(
                creator=self.user,
                assignee=self.user,
                school=self.school,
                is_platform=False,
                title=f"{raw_q} {index}",
                body="تذكرة مطابقة لاختبار ترميز q في روابط assigned_to_me.",
                status=Ticket.Status.DONE,
            )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {
                "q": raw_q,
                "status": Ticket.Status.DONE,
                "order": "created_at",
                "view": "table",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"?q={encoded_q}&status=done&order=created_at&view=list",
        )
        self.assertContains(
            response,
            f"?page=2&view=table&q={encoded_q}&status=done&order=created_at",
        )

    def test_assigned_to_me_clear_button_resets_filters_and_keeps_current_view(self):
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="زر المسح - assigned",
            body="تذكرة لاختبار رابط زر المسح.",
            status=Ticket.Status.DONE,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(
            reverse("reports:assigned_to_me"),
            {
                "q": "زر المسح",
                "status": Ticket.Status.DONE,
                "order": "created_at",
                "view": "table",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مسح")
        self.assertContains(response, 'href="?view=table" class="lx-btn lx-btn-secondary"')

    def test_assigned_to_me_clear_button_is_hidden_without_active_filters_or_order(self):
        Ticket.objects.create(
            creator=self.user,
            assignee=self.user,
            school=self.school,
            is_platform=False,
            title="بدون مسح - assigned",
            body="تذكرة لاختبار اختفاء زر المسح.",
            status=Ticket.Status.OPEN,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        response = self.client.get(reverse("reports:assigned_to_me"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ">مسح<", html=False)
