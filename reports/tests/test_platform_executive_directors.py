# -*- coding: utf-8 -*-
"""شاشة مدير النظام لإدارة المدراء التنفيذيين.

الخاصية التي تحرسها هذه الاختبارات: إنشاء المنصب وإسناد المدارس ملكٌ لمالك
المنصة وحده، وأن ما تُسنده الشاشة هو بالضبط ما يراه المدير التنفيذي في لوحته —
لا أكثر ولا أقل.
"""
from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolGroup,
    SchoolGroupMembership,
    SchoolMembership,
    Teacher,
)
from reports.permissions import executive_director_schools_qs, is_executive_director


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlatformExecutiveDirectorTests(TestCase):
    def setUp(self):
        self.owner = Teacher.objects.create_superuser(
            phone="0500000000", name="مالك المنصة", password="Passw0rd!123"
        )
        self.school_a = School.objects.create(name="مدرسة الأولى", code="school-a")
        self.school_b = School.objects.create(name="مدرسة الثانية", code="school-b")
        self.school_c = School.objects.create(name="مدرسة الثالثة", code="school-c")

        self.list_url = reverse("reports:platform_executive_directors")
        self.add_url = reverse("reports:platform_executive_director_add")

    # ------------------------------------------------------------ helpers
    def _payload(self, **overrides) -> dict:
        data = {
            "group_name": "مجمع النور",
            "group_code": "",
            "education_department": "إدارة تعليم الرياض",
            "is_active": "on",
            "director_phone": "0511111111",
            "director_name": "مدير تنفيذي",
            "director_email": "",
            "director_password": "Passw0rd!123",
            "schools": [str(self.school_a.pk), str(self.school_b.pk)],
            "headquarters_school": "",
        }
        data.update(overrides)
        return data

    def _create_group(self, **overrides) -> SchoolGroup:
        self.client.force_login(self.owner)
        response = self.client.post(self.add_url, self._payload(**overrides))
        self.assertEqual(response.status_code, 302)
        return SchoolGroup.objects.get(name=overrides.get("group_name", "مجمع النور"))

    # ------------------------------------------------------------ الصلاحية
    def test_only_the_platform_owner_reaches_the_screens(self):
        outsider = Teacher.objects.create_user(
            phone="0522222222", name="مدير مدرسة", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school_a, teacher=outsider, role_type=SchoolMembership.RoleType.MANAGER
        )
        self.client.force_login(outsider)

        for url in (self.list_url, self.add_url):
            self.assertEqual(self.client.get(url).status_code, 302)

        # ولا يُنشئ المنصب عبر POST مباشر.
        self.client.post(self.add_url, self._payload())
        self.assertFalse(SchoolGroup.objects.exists())

    def test_an_executive_director_cannot_manage_the_screen(self):
        """اللوحة التي يقرأ منها المدير التنفيذي لا تعطيه سلطة توسيع نطاقه."""
        group = self._create_group()
        director = Teacher.objects.get(phone="0511111111")
        self.client.force_login(director)

        response = self.client.post(
            reverse("reports:platform_executive_director_edit", args=[group.pk]),
            self._payload(schools=[str(self.school_c.pk)]),
        )

        self.assertEqual(response.status_code, 302)
        self.school_c.refresh_from_db()
        self.assertIsNone(self.school_c.group_id)

    # ------------------------------------------------------------ الإنشاء
    def test_adding_a_director_creates_the_account_the_post_and_the_assignment(self):
        group = self._create_group()
        director = Teacher.objects.get(phone="0511111111")

        self.assertTrue(group.is_active)
        self.assertTrue(group.code, "لم يُولَّد معرّف للمجموعة")
        self.assertTrue(is_executive_director(director, group))
        self.assertEqual(
            set(executive_director_schools_qs(director).values_list("code", flat=True)),
            {"school-a", "school-b"},
        )
        # المدرسة غير المختارة تبقى خارج نطاقه.
        self.school_c.refresh_from_db()
        self.assertIsNone(self.school_c.group_id)
        # ولا يصير مديراً لأي مدرسة بحكم المنصب.
        self.assertFalse(SchoolMembership.objects.filter(teacher=director).exists())

    def test_an_existing_account_is_linked_not_duplicated(self):
        existing = Teacher.objects.create_user(
            phone="0533333333", name="حساب قائم", password="Passw0rd!123"
        )
        self._create_group(director_phone="0533333333", director_password="")

        self.assertEqual(Teacher.objects.filter(phone="0533333333").count(), 1)
        self.assertTrue(is_executive_director(Teacher.objects.get(pk=existing.pk)))
        # كلمة المرور القائمة لا تُمس عندما يُترك الحقل فارغاً.
        self.assertTrue(
            Teacher.objects.get(pk=existing.pk).check_password("Passw0rd!123")
        )

    def test_a_new_account_requires_a_name_and_a_password(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.add_url, self._payload(director_name="", director_password="")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SchoolGroup.objects.exists())
        self.assertFalse(Teacher.objects.filter(phone="0511111111").exists())

    def test_a_person_cannot_lead_two_groups_at_once(self):
        self._create_group()
        self.client.force_login(self.owner)

        response = self.client.post(
            self.add_url, self._payload(group_name="مجمع الفجر", schools=[str(self.school_c.pk)])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SchoolGroup.objects.filter(name="مجمع الفجر").exists())

    def test_headquarters_must_be_one_of_the_assigned_schools(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.add_url, self._payload(headquarters_school=str(self.school_c.pk))
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SchoolGroup.objects.exists())

    # ------------------------------------------------------------ التعديل
    def test_editing_reassigns_schools_in_both_directions(self):
        group = self._create_group()
        director = Teacher.objects.get(phone="0511111111")
        edit_url = reverse("reports:platform_executive_director_edit", args=[group.pk])

        self.client.force_login(self.owner)
        response = self.client.post(
            edit_url,
            self._payload(
                schools=[str(self.school_b.pk), str(self.school_c.pk)],
                headquarters_school=str(self.school_c.pk),
                director_password="",
            ),
        )

        self.assertEqual(response.status_code, 302)
        director = Teacher.objects.get(pk=director.pk)
        self.assertEqual(
            set(executive_director_schools_qs(director).values_list("code", flat=True)),
            {"school-b", "school-c"},
        )
        # المدرسة المرفوعة تعود مستقلة بلا مساس ببياناتها.
        self.school_a.refresh_from_db()
        self.assertIsNone(self.school_a.group_id)
        self.assertTrue(self.school_a.is_active)
        group.refresh_from_db()
        self.assertEqual(group.headquarters_school_id, self.school_c.pk)

    def test_replacing_the_director_retires_the_previous_one(self):
        group = self._create_group()
        previous = Teacher.objects.get(phone="0511111111")

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("reports:platform_executive_director_edit", args=[group.pk]),
            self._payload(
                director_phone="0544444444",
                director_name="مدير تنفيذي جديد",
                director_password="Passw0rd!123",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(is_executive_director(Teacher.objects.get(pk=previous.pk)))
        self.assertTrue(is_executive_director(Teacher.objects.get(phone="0544444444"), group))
        self.assertEqual(
            SchoolGroupMembership.objects.filter(group=group, is_active=True).count(), 1
        )

    # ------------------------------------------------------------ التحكم
    def test_toggling_suspends_and_restores_the_directorship(self):
        group = self._create_group()
        director = Teacher.objects.get(phone="0511111111")
        toggle_url = reverse("reports:platform_executive_director_toggle", args=[group.pk])

        self.client.force_login(self.owner)
        self.client.post(toggle_url)

        suspended = Teacher.objects.get(pk=director.pk)
        self.assertFalse(is_executive_director(suspended))
        # الإيقاف يُغلق اللوحة في وجه صاحبها فوراً.
        self.client.force_login(suspended)
        self.assertEqual(self.client.get(reverse("reports:executive_dashboard")).status_code, 404)
        # والمدارس لا تتأثر بإيقاف طبقة الإشراف.
        self.school_a.refresh_from_db()
        self.assertTrue(self.school_a.is_active)

        self.client.force_login(self.owner)
        self.client.post(toggle_url)
        self.assertTrue(is_executive_director(Teacher.objects.get(pk=director.pk)))

    def test_deleting_the_group_frees_the_schools_and_keeps_the_account(self):
        group = self._create_group()
        director = Teacher.objects.get(phone="0511111111")

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("reports:platform_executive_director_delete", args=[group.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolGroup.objects.filter(pk=group.pk).exists())
        for school in (self.school_a, self.school_b):
            school.refresh_from_db()
            self.assertIsNone(school.group_id)
            self.assertTrue(school.is_active)
        self.assertTrue(Teacher.objects.filter(pk=director.pk).exists())
        self.assertFalse(is_executive_director(Teacher.objects.get(pk=director.pk)))

    # ------------------------------------------------------------ العرض
    def test_the_list_shows_the_director_and_their_schools(self):
        self._create_group()
        self.client.force_login(self.owner)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مجمع النور")
        self.assertContains(response, "0511111111")
        self.assertContains(response, self.school_a.name)
        self.assertEqual(response.context["stats"]["active_directors"], 1)
        self.assertEqual(response.context["stats"]["schools"], 2)

    def test_the_edit_screen_opens_prefilled(self):
        group = self._create_group()
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("reports:platform_executive_director_edit", args=[group.pk])
        )

        self.assertEqual(response.status_code, 200)
        initial = response.context["form"].initial
        self.assertEqual(initial["director_phone"], "0511111111")
        self.assertEqual(set(initial["schools"]), {self.school_a.pk, self.school_b.pk})

    def test_search_narrows_the_list_by_director_phone(self):
        self._create_group()
        self._create_group(
            group_name="مجمع الفجر",
            director_phone="0555555555",
            director_name="مدير آخر",
            schools=[str(self.school_c.pk)],
        )
        self.client.force_login(self.owner)

        response = self.client.get(self.list_url, {"q": "0555555555"})

        names = [row["group"].name for row in response.context["rows"]]
        self.assertEqual(names, ["مجمع الفجر"])
