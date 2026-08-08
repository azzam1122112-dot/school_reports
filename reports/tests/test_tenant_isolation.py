"""حراسة العزل بين المدارس وداخلها.

هذا الملف ليس اختباراً لميزة، بل **شبكة أمان لقاعدة معمارية**: لا يرى مستخدمٌ
سجلاً خارج مدرسته النشطة، ولا يرى داخلها إلا ما هو طرفٌ فيه.

الحاجة إليه أن العزل في هذا المشروع مفروضٌ في كل شاشة على حدة — وهو التصميم
الصحيح، لأن المركزةَ الكاملة تعني ManagerاًSQL واحداً يعرف كل الموديلات. لكن ما
يُفرض في كل شاشة يمكن أن يُنسى في الشاشة القادمة، ولا شيء يكشف النسيان إلا
اختبارٌ يحاول الاختراق فعلاً.

فكل اختبار هنا يبني مدرستين كاملتين ثم يحاول الوصول من إحداهما إلى الأخرى
برقم السجل مباشرة، لا عبر واجهة تُخفي الزر.
"""
from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    Report,
    ReportType,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
    Ticket,
)
from reports.permissions import restrict_queryset_for_user
from reports.services_reports import get_report_for_user_or_404


class _TwoSchoolFixture(TestCase):
    """مدرستان مكتملتان: اشتراك ساري، ومدير ومعلّم لكلٍّ منهما.

    الاشتراك ليس تفصيلاً في التجهيز: ``SubscriptionMiddleware`` يحوّل كل طلب من
    مدرسة منتهية إلى صفحة التجديد، فتنجح اختبارات العزل لسببٍ خاطئ — المنع جاء
    من الاشتراك لا من العزل.

    و**تكرار كود نوع التقرير في المدرستين مقصود**: أكواد الأنواع مخصَّصة لكل
    مدرسة وقد تتصادم، وهي بالضبط الحالة التي كانت تجعل الفلترة بالكود وحدها
    تسرّب تقارير عبر المدارس.
    """

    SHARED_CATEGORY_CODE = "radio"

    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )

        self.school_a = School.objects.create(name="مدرسة أ", code="school-a")
        self.school_b = School.objects.create(name="مدرسة ب", code="school-b")
        SchoolSubscription.objects.create(school=self.school_a, plan=plan)
        SchoolSubscription.objects.create(school=self.school_b, plan=plan)

        self.manager_a = self._member(
            self.school_a, "500100001", "مدير أ", SchoolMembership.RoleType.MANAGER
        )
        self.teacher_a = self._member(
            self.school_a, "500100002", "معلم أ", SchoolMembership.RoleType.TEACHER
        )
        self.teacher_a2 = self._member(
            self.school_a, "500100003", "معلم أ٢", SchoolMembership.RoleType.TEACHER
        )
        self.manager_b = self._member(
            self.school_b, "500200001", "مدير ب", SchoolMembership.RoleType.MANAGER
        )
        self.teacher_b = self._member(
            self.school_b, "500200002", "معلم ب", SchoolMembership.RoleType.TEACHER
        )

        self.category_a = ReportType.objects.create(
            school=self.school_a, code=self.SHARED_CATEGORY_CODE, name="الإذاعة"
        )
        self.category_b = ReportType.objects.create(
            school=self.school_b, code=self.SHARED_CATEGORY_CODE, name="الإذاعة"
        )

        self.report_a = Report.objects.create(
            school=self.school_a,
            teacher=self.teacher_a,
            title="تقرير مدرسة أ",
            idea="فعالية",
            category=self.category_a,
            report_date=date(2025, 1, 1),
        )
        self.report_b = Report.objects.create(
            school=self.school_b,
            teacher=self.teacher_b,
            title="تقرير مدرسة ب",
            idea="فعالية",
            category=self.category_b,
            report_date=date(2025, 1, 2),
        )

    def _member(self, school, phone, name, role_type):
        user = Teacher.objects.create_user(phone=phone, name=name, password="pass")
        SchoolMembership.objects.create(school=school, teacher=user, role_type=role_type)
        return user

    def _login(self, user, school):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = school.id
        session.save()


@override_settings(ALLOWED_HOSTS=["testserver"])
class ReportIsolationTests(_TwoSchoolFixture):
    def test_manager_cannot_open_report_of_another_school(self):
        self._login(self.manager_a, self.school_a)
        response = self.client.get(
            reverse("reports:report_print", args=[self.report_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_manager_cannot_edit_report_of_another_school(self):
        self._login(self.manager_a, self.school_a)
        response = self.client.get(
            reverse("reports:edit_my_report", args=[self.report_b.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_restrict_queryset_scopes_to_school_without_help_from_caller(self):
        """الدالة معزولة وحدها، لا بفضل فلترٍ يضيفه المستدعي بعدها.

        هذا هو جوهر الإصلاح: كودُ النوع نفسه موجود في المدرستين، فلو كانت
        الفلترة بالكود وحدها لعادت تقارير المدرستين معاً.
        """
        visible = restrict_queryset_for_user(
            Report.objects.all(), self.manager_a, self.school_a
        )
        self.assertIn(self.report_a, visible)
        self.assertNotIn(self.report_b, visible)

    def test_report_lookup_without_active_school_returns_only_own_reports(self):
        """غياب المدرسة النشطة يضيّق النطاق ولا يوسّعه."""
        with self.assertRaises(Http404):
            get_report_for_user_or_404(
                user=self.manager_a, pk=self.report_b.pk, active_school=None
            )

    def test_is_staff_flag_does_not_bypass_school_scope(self):
        """``is_staff`` عَلَم إداري لا يمنح عبوراً بين المستأجرين.

        كان الشرط ``if user.is_staff: return get_object_or_404(qs, pk=pk)`` بلا
        فلتر مدرسة حين تغيب المدرسة النشطة — و``is_staff`` يُمنح من لوحة إدارة
        Django لمن ليس مالكاً للنظام.
        """
        self.manager_a.is_staff = True
        self.manager_a.save(update_fields=["is_staff"])
        with self.assertRaises(Http404):
            get_report_for_user_or_404(
                user=self.manager_a, pk=self.report_b.pk, active_school=None
            )


@override_settings(ALLOWED_HOSTS=["testserver"])
class TicketAccessTests(_TwoSchoolFixture):
    def setUp(self):
        super().setUp()
        self.ticket_a = Ticket.objects.create(
            creator=self.teacher_a,
            school=self.school_a,
            is_platform=False,
            title="طلب خاص",
            body="أمر شخصي بيني وبين الإدارة.",
        )

    def test_member_of_another_school_cannot_read_ticket(self):
        self._login(self.teacher_b, self.school_b)
        response = self.client.get(
            reverse("reports:ticket_detail", args=[self.ticket_a.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_uninvolved_colleague_in_same_school_cannot_read_ticket(self):
        """الحدّ داخل المدرسة لا عبرها فقط.

        كانت بوابة القراءة هي العضوية في المدرسة وحدها، فيفتح أي زميل طلب زميله
        برقمه ويقرأ نصّه وملاحظاته.
        """
        self._login(self.teacher_a2, self.school_a)
        response = self.client.get(
            reverse("reports:ticket_detail", args=[self.ticket_a.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_uninvolved_colleague_cannot_print_ticket(self):
        """الطباعة تكشف المحتوى نفسه، فتحمل البوابة نفسها."""
        self._login(self.teacher_a2, self.school_a)
        response = self.client.get(
            reverse("reports:ticket_print", args=[self.ticket_a.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_owner_can_read_own_ticket(self):
        self._login(self.teacher_a, self.school_a)
        response = self.client.get(
            reverse("reports:ticket_detail", args=[self.ticket_a.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_school_manager_can_read_ticket_of_own_school(self):
        """التشديد لم يُغلق الباب على من يلزمه فتحه."""
        self._login(self.manager_a, self.school_a)
        response = self.client.get(
            reverse("reports:ticket_detail", args=[self.ticket_a.pk])
        )
        self.assertEqual(response.status_code, 200)


@override_settings(ALLOWED_HOSTS=["testserver"])
class TeacherAdministrationIsolationTests(_TwoSchoolFixture):
    def test_manager_cannot_edit_staff_of_another_school(self):
        self._login(self.manager_a, self.school_a)
        response = self.client.get(
            reverse("reports:edit_teacher", args=[self.teacher_b.pk])
        )
        self.assertRedirects(
            response, reverse("reports:manage_teachers"), fetch_redirect_response=False
        )

    def test_manager_cannot_delete_staff_of_another_school(self):
        self._login(self.manager_a, self.school_a)
        self.client.post(reverse("reports:delete_teacher", args=[self.teacher_b.pk]))
        self.assertTrue(Teacher.objects.filter(pk=self.teacher_b.pk).exists())
        self.assertTrue(
            SchoolMembership.objects.filter(
                school=self.school_b, teacher=self.teacher_b, is_active=True
            ).exists()
        )

    def test_delete_without_active_school_never_wipes_the_account(self):
        """بلا مدرسة نشطة لا يُحذف حساب من المنصة — دفاعٌ على طبقتين.

        الشرط كان ``and active_school is not None``، فغيابُها يُسقط فحص الارتباط
        ويصل التنفيذ إلى فرع ``teacher.delete()`` — حذفِ الحساب من المنصة كلها لا
        فصلِه عن مدرسة. ``role_required`` يردّ الطلب أولاً، وفحصُ الشاشة صار
        يردّه ثانياً، والاختبار يحرس النتيجة لا الطبقة التي حقّقتها.
        """
        self.client.force_login(self.manager_a)
        session = self.client.session
        session.pop("active_school_id", None)
        session.save()

        self.client.post(reverse("reports:delete_teacher", args=[self.teacher_a.pk]))
        self.assertTrue(Teacher.objects.filter(pk=self.teacher_a.pk).exists())
        self.assertTrue(
            SchoolMembership.objects.filter(
                school=self.school_a, teacher=self.teacher_a, is_active=True
            ).exists()
        )


@override_settings(ALLOWED_HOSTS=["testserver"])
class SwitchSchoolTests(_TwoSchoolFixture):
    def test_switch_school_rejects_get(self):
        """تبديل المدرسة تغييرُ حالة، وGET بلا حماية CSRF.

        رابطٌ في صفحة أجنبية كان يكفي لتبديل المدرسة النشطة بصمت، فيذهب التعميم
        التالي إلى مدرسة لم يقصدها صاحبه.
        """
        self._login(self.manager_a, self.school_a)
        response = self.client.get(
            reverse("reports:switch_school"), {"school_id": self.school_b.id}
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.session.get("active_school_id"), self.school_a.id)

    def test_switch_school_rejects_school_without_membership(self):
        outsider_school = School.objects.create(name="مدرسة ج", code="school-c")
        self._login(self.manager_a, self.school_a)
        self.client.post(
            reverse("reports:switch_school"), {"school_id": outsider_school.id}
        )
        self.assertEqual(self.client.session.get("active_school_id"), self.school_a.id)


@override_settings(ALLOWED_HOSTS=["testserver"])
class LoginThrottleTests(TestCase):
    """الخنق على مستوى الحساب — ما لا يمسكه حدُّ الـ IP."""

    def setUp(self):
        # الذاكرة المؤقتة تحمل عدّادَي الخنق: عدّاد الـ IP الذي يستعمله
        # django-ratelimit وعدّاد الحساب. تنظيفها يعزل كل اختبار عن سابقه.
        cache.clear()
        self.user = Teacher.objects.create_user(
            phone="500900001", name="مستخدم", password="correct-horse-battery"
        )

    def test_locked_account_refuses_even_the_correct_password(self):
        """الإقفال يسبق فحص كلمة المرور، وإلا ما أبطأ التخمين شيئاً.

        الإخفاقات تُسجَّل مباشرةً لا بطلبات متتالية: حدُّ الـ IP يمنع تكرار
        الطلب عشر مرات في الدقيقة، فلو أُرسلت لَحجبها الحدُّ الأول وصار
        الاختبار يقيس غير ما يدّعي.
        """
        from reports.views.auth import (
            LOGIN_ACCOUNT_MAX_FAILURES,
            _login_account_locked,
            _register_login_failure,
        )

        # طلب حقيقي واحد يثبت أن الشاشة نفسها تُغذّي العدّاد.
        self.client.post(
            reverse("reports:login"), {"phone": self.user.phone, "password": "wrong"}
        )
        for _ in range(LOGIN_ACCOUNT_MAX_FAILURES - 1):
            _register_login_failure(self.user.phone)

        self.assertTrue(_login_account_locked(self.user.phone))

        self.client.post(
            reverse("reports:login"),
            {"phone": self.user.phone, "password": "correct-horse-battery"},
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_login_clears_the_failure_counter(self):
        """صاحب الحساب لا يُقفل على نفسه بمحاولةٍ نسي فيها كلمته."""
        from reports.views.auth import _login_account_locked

        self.client.post(
            reverse("reports:login"), {"phone": self.user.phone, "password": "wrong"}
        )
        self.client.post(
            reverse("reports:login"),
            {"phone": self.user.phone, "password": "correct-horse-battery"},
        )
        self.assertFalse(_login_account_locked(self.user.phone))
        self.assertIn("_auth_user_id", self.client.session)
