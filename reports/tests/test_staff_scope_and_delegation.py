# -*- coding: utf-8 -*-
"""نطاق الصلاحية والتفويض المؤقت.

الخصائص المحروسة هنا هي **الحدود** لا القدرات: ما الذي لا يستطيعه الوكيل، وما
الذي ينتهي بذاته من التفويض، وما الذي لا يُخزَّن أصلاً من الرموز المجهولة.
فقدرةٌ زائدة في نظام صلاحيات أخطر من ميزة ناقصة.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports import capabilities as caps
from reports.models import (
    Delegation,
    Department,
    School,
    SchoolMembership,
    SchoolSubscription,
    StaffScope,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import (
    capability_source,
    delegated_capabilities,
    has_capability,
    scope_capabilities,
    supervised_department_ids,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


def _school(name: str, code: str) -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=0
    )
    school = School.objects.create(name=name, code=code)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


class CapabilityCatalogTests(TestCase):
    """مرجع الصلاحيات: سلطة الكود الوحيدة."""

    def test_every_template_references_only_known_capabilities(self):
        """قالب يشير إلى رمز غير معرَّف يمنح صلاحيةً لا يقرؤها أحد."""
        for template in caps.TEMPLATES:
            unknown = set(template.capabilities) - caps.VALID_CODES
            self.assertEqual(unknown, set(), f"القالب {template.code} يحمل رموزاً مجهولة")

    def test_every_template_matches_its_roles_allowed_capabilities(self):
        for template in caps.TEMPLATES:
            allowed = {item.code for item in caps.capabilities_for_role(template.role)}
            self.assertTrue(
                set(template.capabilities) <= allowed,
                f"القالب {template.code} يمنح ما لا يجوز لدوره",
            )

    def test_sanitize_drops_unknown_codes(self):
        self.assertEqual(caps.sanitize(["not_a_capability"]), [])

    def test_sanitize_is_order_stable(self):
        """ترتيب ثابت: وإلا بدا سجلّان متطابقان في المعنى مختلفين في التخزين."""
        forward = caps.sanitize([caps.VIEW_AUDIT_LOG, caps.VIEW_SCHOOL_DASHBOARD])
        backward = caps.sanitize([caps.VIEW_SCHOOL_DASHBOARD, caps.VIEW_AUDIT_LOG])
        self.assertEqual(forward, backward)

    def test_sanitize_respects_role_limits(self):
        """``assign_tasks`` للوكيل وحده — فلا يُخزَّن لموظف إداري."""
        self.assertEqual(caps.sanitize([caps.ASSIGN_TASKS], role="admin_staff"), [])
        self.assertEqual(caps.sanitize([caps.ASSIGN_TASKS], role="deputy"), [caps.ASSIGN_TASKS])


class StaffScopeTests(TestCase):
    def setUp(self):
        self.school = _school("مدرسة النطاق", "scope-school")
        self.deputy = _user("الوكيل", "0500010001")
        self.membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )

    def test_unknown_capability_codes_are_never_stored(self):
        scope = StaffScope.objects.create(
            membership=self.membership,
            capabilities=[caps.VIEW_AUDIT_LOG, "make_me_admin"],
        )
        scope.refresh_from_db()
        self.assertEqual(scope.capabilities, [caps.VIEW_AUDIT_LOG])

    def test_pending_capabilities_are_stored_but_not_effective(self):
        """اختيار المدير يُحفظ، لكن الصلاحية الجوفاء لا تُعدّ نافذة.

        كل الصلاحيات صارت نافذة اليوم، فلا توجد واحدة معلَّقة يُبنى عليها
        الاختبار. والسلوك يبقى مطلوباً لأي صلاحية تُضاف لاحقاً قبل أن تُبنى
        ميزتها — فنحاكي التعليق بدل انتظار وقوعه، ولو رُبط الاختبار بوجود
        معلَّقة فعلية لانكسر عند أول اكتمال بدل أن يحرس القاعدة.
        """
        from unittest.mock import patch

        pending = caps.VIEW_AUDIT_LOG
        scope = StaffScope.objects.create(membership=self.membership, capabilities=[pending])

        available_without_it = frozenset(caps.AVAILABLE_CODES) - {pending}
        with patch.object(caps, "AVAILABLE_CODES", available_without_it):
            self.assertIn(pending, scope.capabilities, "الاختيار يُحفظ")
            self.assertNotIn(pending, scope.capability_codes(), "ولا يُعدّ نافذاً")

    def test_every_capability_is_now_available(self):
        """معلَم: لم تعد أي صلاحية موسومة «قريباً».

        وحين تُضاف صلاحية جديدة لميزة لم تُبنَ بعد سيفشل هذا الاختبار — وهو
        تذكيرٌ مقصود بأن الوسم مؤقت لا حالة دائمة.
        """
        pending = [item.code for item in caps.ALL if not item.available]
        self.assertEqual(pending, [], f"صلاحيات معلَّقة: {pending}")

    def test_a_domain_cannot_be_set_on_a_non_deputy(self):
        clerk = _user("موظف", "0500010002")
        membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=clerk,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
        )
        scope = StaffScope(membership=membership, domain=StaffScope.Domain.ACADEMIC)

        with self.assertRaises(ValidationError):
            scope.full_clean()

    def test_a_scope_dies_with_its_membership(self):
        """نطاق وكيلٍ لم يعد وكيلاً ليس له معنى."""
        StaffScope.objects.create(membership=self.membership, capabilities=[caps.VIEW_AUDIT_LOG])
        self.membership.delete()
        self.assertFalse(StaffScope.objects.exists())

    def test_scope_capabilities_are_school_scoped(self):
        StaffScope.objects.create(membership=self.membership, capabilities=[caps.VIEW_AUDIT_LOG])
        elsewhere = _school("مدرسة أخرى", "scope-other")

        self.assertIn(caps.VIEW_AUDIT_LOG, scope_capabilities(self.deputy, self.school))
        self.assertEqual(scope_capabilities(self.deputy, elsewhere), set())

    def test_empty_departments_means_none_not_all(self):
        """التأويل المعاكس يحوّل نطاقاً لم يُضبط إلى صلاحية على المدرسة كلها."""
        StaffScope.objects.create(membership=self.membership)
        self.assertEqual(supervised_department_ids(self.deputy, self.school), set())

    def test_supervised_departments_are_reported(self):
        department = Department.objects.create(school=self.school, name="النشاط", slug="activity")
        scope = StaffScope.objects.create(membership=self.membership)
        scope.departments.add(department)

        self.assertEqual(
            supervised_department_ids(self.deputy, self.school), {department.pk}
        )


class DelegationTests(TestCase):
    def setUp(self):
        self.school = _school("مدرسة التفويض", "deleg-school")
        self.manager = _user("المدير", "0500011001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.deputy = _user("الوكيل", "0500011002")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )

    def _delegate(self, **overrides):
        now = timezone.now()
        data = {
            "school": self.school,
            "delegator": self.manager,
            "delegate": self.deputy,
            "capabilities": [caps.VIEW_AUDIT_LOG],
            "starts_at": now - timedelta(hours=1),
            "ends_at": now + timedelta(days=3),
        }
        data.update(overrides)
        return Delegation.objects.create(**data)

    def test_an_active_delegation_grants_its_capabilities(self):
        self._delegate()
        self.assertIn(caps.VIEW_AUDIT_LOG, delegated_capabilities(self.deputy, self.school))

    def test_an_expired_delegation_grants_nothing(self):
        """ينتهي بذاته زمنياً — لا بمهمة مجدولة قد لا تعمل."""
        now = timezone.now()
        self._delegate(starts_at=now - timedelta(days=9), ends_at=now - timedelta(days=2))
        self.assertEqual(delegated_capabilities(self.deputy, self.school), set())

    def test_a_future_delegation_grants_nothing_yet(self):
        now = timezone.now()
        self._delegate(starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=5))
        self.assertEqual(delegated_capabilities(self.deputy, self.school), set())

    def test_revoking_stops_it_immediately_without_erasing_it(self):
        delegation = self._delegate()
        delegation.revoke(by=self.manager)

        self.assertEqual(delegated_capabilities(self.deputy, self.school), set())
        self.assertTrue(Delegation.objects.filter(pk=delegation.pk).exists())
        self.assertEqual(delegation.state, "revoked")

    def test_a_delegation_must_end_after_it_starts(self):
        now = timezone.now()
        delegation = Delegation(
            school=self.school,
            delegator=self.manager,
            delegate=self.deputy,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=now,
            ends_at=now - timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            delegation.full_clean()

    def test_an_outsider_cannot_be_delegated_to(self):
        """التفويض لمن لا عضوية له يفتح المدرسة على غريب."""
        outsider = _user("غريب", "0500011003")
        delegation = Delegation(
            school=self.school,
            delegator=self.manager,
            delegate=outsider,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            delegation.full_clean()

    def test_a_manager_cannot_delegate_to_themselves(self):
        delegation = Delegation(
            school=self.school,
            delegator=self.manager,
            delegate=self.manager,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            delegation.full_clean()

    def test_delegation_is_confined_to_its_school(self):
        self._delegate()
        elsewhere = _school("مدرسة بعيدة", "deleg-other")
        self.assertEqual(delegated_capabilities(self.deputy, elsewhere), set())


class CapabilitySourceTests(TestCase):
    """التمييز بين ما يُمارَس بالأصالة وما يُمارَس بالنيابة."""

    def setUp(self):
        self.school = _school("مدرسة المصدر", "source-school")
        self.manager = _user("المدير", "0500012001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.deputy = _user("الوكيل", "0500012002")
        self.membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )

    def test_a_scoped_capability_reports_scope_as_its_source(self):
        StaffScope.objects.create(membership=self.membership, capabilities=[caps.VIEW_AUDIT_LOG])
        self.assertEqual(
            capability_source(self.deputy, caps.VIEW_AUDIT_LOG, self.school), "scope"
        )

    def test_a_delegated_capability_reports_delegation_as_its_source(self):
        Delegation.objects.create(
            school=self.school,
            delegator=self.manager,
            delegate=self.deputy,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(
            capability_source(self.deputy, caps.VIEW_AUDIT_LOG, self.school), "delegation"
        )

    def test_owning_it_outright_wins_over_holding_it_by_proxy(self):
        """من يملكها أصالةً لا يُسجَّل عمله كأنه نيابة عن غيره."""
        StaffScope.objects.create(membership=self.membership, capabilities=[caps.VIEW_AUDIT_LOG])
        Delegation.objects.create(
            school=self.school,
            delegator=self.manager,
            delegate=self.deputy,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(
            capability_source(self.deputy, caps.VIEW_AUDIT_LOG, self.school), "scope"
        )

    def test_a_manager_holds_every_capability_in_their_own_school(self):
        self.assertTrue(has_capability(self.manager, caps.VIEW_AUDIT_LOG, self.school))

    def test_a_bare_role_grants_nothing(self):
        """حمل الدور ليس صلاحية — النطاق هو الذي يمنح."""
        self.assertFalse(has_capability(self.deputy, caps.VIEW_AUDIT_LOG, self.school))


@override_settings(ALLOWED_HOSTS=["testserver"])
class StaffRolesScreenTests(TestCase):
    """الشاشة: من يدخلها، وما الذي تمنعه على المدير نفسه."""

    def setUp(self):
        self.school = _school("مدرسة الشاشة", "screen-school")
        self.manager = _user("المدير", "0500013001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = _user("معلم", "0500013002")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.url = reverse("reports:staff_roles")

    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_the_manager_can_open_the_screen(self):
        self._enter(self.manager)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الأدوار والصلاحيات")

    def test_a_teacher_cannot_open_the_screen(self):
        self._enter(self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_assigning_a_deputy_role_replaces_the_previous_one(self):
        self._enter(self.manager)
        response = self.client.post(
            self.url,
            {
                "action": "assign_role",
                "member": self.teacher.pk,
                "role_type": SchoolMembership.RoleType.DEPUTY,
            },
        )
        self.assertEqual(response.status_code, 302)

        roles = set(
            SchoolMembership.objects.filter(
                school=self.school, teacher=self.teacher
            ).values_list("role_type", flat=True)
        )
        self.assertEqual(roles, {SchoolMembership.RoleType.DEPUTY})

    def test_keeping_the_teaching_load_preserves_both_roles(self):
        self._enter(self.manager)
        self.client.post(
            self.url,
            {
                "action": "assign_role",
                "member": self.teacher.pk,
                "role_type": SchoolMembership.RoleType.DEPUTY,
                "keep_teaching_role": "on",
            },
        )

        roles = set(
            SchoolMembership.objects.filter(
                school=self.school, teacher=self.teacher
            ).values_list("role_type", flat=True)
        )
        self.assertEqual(
            roles,
            {SchoolMembership.RoleType.DEPUTY, SchoolMembership.RoleType.TEACHER},
        )

    def test_the_manager_role_is_not_assignable_from_this_screen(self):
        """نقل الإدارة قرار خارج المدرسة — ولو أُتيح لاستطاع المدير عزل نفسه."""
        self._enter(self.manager)
        self.client.post(
            self.url,
            {
                "action": "assign_role",
                "member": self.teacher.pk,
                "role_type": SchoolMembership.RoleType.MANAGER,
            },
        )
        self.assertFalse(
            SchoolMembership.objects.filter(
                school=self.school,
                teacher=self.teacher,
                role_type=SchoolMembership.RoleType.MANAGER,
            ).exists()
        )

    def test_a_member_from_another_school_is_rejected(self):
        elsewhere = _school("مدرسة بعيدة", "screen-other")
        outsider = _user("غريب", "0500013003")
        SchoolMembership.objects.create(
            school=elsewhere,
            teacher=outsider,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self._enter(self.manager)

        self.client.post(
            self.url,
            {
                "action": "assign_role",
                "member": outsider.pk,
                "role_type": SchoolMembership.RoleType.DEPUTY,
            },
        )
        self.assertFalse(
            SchoolMembership.objects.filter(school=self.school, teacher=outsider).exists()
        )

    def test_the_scope_screen_refuses_a_plain_teacher(self):
        membership = SchoolMembership.objects.get(
            school=self.school, teacher=self.teacher
        )
        self._enter(self.manager)

        response = self.client.get(
            reverse("reports:staff_role_scope", args=[membership.pk])
        )
        self.assertEqual(response.status_code, 302)

    def test_the_scope_screen_opens_for_a_deputy(self):
        membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=_user("وكيل", "0500013004"),
            role_type=SchoolMembership.RoleType.DEPUTY,
        )
        self._enter(self.manager)

        response = self.client.get(
            reverse("reports:staff_role_scope", args=[membership.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "القوالب المعتمدة")

    def test_applying_a_template_fills_the_form_without_saving(self):
        """القالب يقترح ولا يحفظ — فلا يُفاجأ المدير بحفظٍ لم يطلبه."""
        deputy = _user("وكيل القالب", "0500013010")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:staff_role_scope", args=[membership.pk]),
            {"action": "apply_template", "template_code": "deputy_academic"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            StaffScope.objects.filter(membership=membership).exists(),
            "تطبيق القالب لا يجوز أن يحفظ",
        )

    def test_saving_a_scope_persists_capabilities_and_departments(self):
        deputy = _user("وكيل الحفظ", "0500013011")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        department = Department.objects.create(
            school=self.school, name="الشؤون التعليمية", slug="academic-dept"
        )
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:staff_role_scope", args=[membership.pk]),
            {
                "action": "save_scope",
                "domain": StaffScope.Domain.ACADEMIC,
                "departments": [department.pk],
                "capabilities": [caps.VIEW_AUDIT_LOG, caps.VIEW_SCHOOL_DASHBOARD],
                "template_code": "",
            },
        )
        self.assertEqual(response.status_code, 302)

        scope = StaffScope.objects.get(membership=membership)
        self.assertEqual(scope.domain, StaffScope.Domain.ACADEMIC)
        self.assertEqual(list(scope.departments.values_list("pk", flat=True)), [department.pk])
        self.assertEqual(
            scope.capabilities, [caps.VIEW_SCHOOL_DASHBOARD, caps.VIEW_AUDIT_LOG]
        )
        self.assertEqual(scope.granted_by_id, self.manager.pk)

    def test_a_tampered_capability_code_is_refused_not_silently_dropped(self):
        """الشاشة تعرض المسموح وحده، فرمزٌ من خارجها يعني طلباً مُتلاعَباً به.

        الإسقاط الصامت هنا خطأ: يجعل الطلب المزوَّر ينجح جزئياً فيبدو مقبولاً.
        أما ``StaffScope.save`` فيُسقط بصمت عن قصد، لأنه يخدم الإنشاء البرمجي
        الذي لا مستخدم خلفه ليُخطَر.
        """
        deputy = _user("وكيل التلاعب", "0500013016")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:staff_role_scope", args=[membership.pk]),
            {
                "action": "save_scope",
                "domain": "",
                "capabilities": [caps.VIEW_AUDIT_LOG, "make_me_admin"],
                "template_code": "",
            },
        )

        self.assertEqual(response.status_code, 200, "الطلب المزوَّر يُرفض ولا يُعاد توجيهه")
        self.assertFalse(StaffScope.objects.filter(membership=membership).exists())

    def test_a_capability_outside_the_role_is_refused(self):
        """``assign_tasks`` للوكيل وحده — فلا تُمنح لموظف إداري ولو أُرسلت يدوياً."""
        clerk = _user("موظف طموح", "0500013017")
        membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=clerk,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
        )
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:staff_role_scope", args=[membership.pk]),
            {
                "action": "save_scope",
                "capabilities": [caps.ASSIGN_TASKS],
                "template_code": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(StaffScope.objects.filter(membership=membership).exists())

    def test_a_granted_scope_takes_effect_immediately(self):
        """الحفظ ليس تسجيلاً شكلياً — الصلاحية تصير نافذة فور حفظها."""
        deputy = _user("وكيل نافذ", "0500013012")
        membership = SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self.assertFalse(has_capability(deputy, caps.VIEW_AUDIT_LOG, self.school))

        self._enter(self.manager)
        self.client.post(
            reverse("reports:staff_role_scope", args=[membership.pk]),
            {
                "action": "save_scope",
                "domain": "",
                "capabilities": [caps.VIEW_AUDIT_LOG],
                "template_code": "",
            },
        )

        fresh = Teacher.objects.get(pk=deputy.pk)
        self.assertTrue(has_capability(fresh, caps.VIEW_AUDIT_LOG, self.school))

    def test_granting_and_revoking_a_delegation_through_the_screen(self):
        deputy = _user("وكيل التفويض", "0500013013")
        SchoolMembership.objects.create(
            school=self.school, teacher=deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self._enter(self.manager)

        starts = timezone.localtime()
        ends = starts + timedelta(days=2)
        response = self.client.post(
            self.url,
            {
                "action": "grant_delegation",
                "delegate": deputy.pk,
                "capabilities": [caps.VIEW_AUDIT_LOG],
                "reason": "إجازة المدير",
                "starts_at": starts.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": ends.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(response.status_code, 302)

        delegation = Delegation.objects.get(school=self.school, delegate=deputy)
        self.assertTrue(delegation.is_active)
        self.assertTrue(has_capability(Teacher.objects.get(pk=deputy.pk), caps.VIEW_AUDIT_LOG, self.school))

        self.client.post(reverse("reports:delegation_revoke", args=[delegation.pk]))
        delegation.refresh_from_db()
        self.assertIsNotNone(delegation.revoked_at)
        self.assertFalse(
            has_capability(Teacher.objects.get(pk=deputy.pk), caps.VIEW_AUDIT_LOG, self.school)
        )

    def test_a_delegation_from_another_school_cannot_be_revoked(self):
        elsewhere = _school("مدرسة أجنبية", "screen-alien")
        alien_manager = _user("مدير آخر", "0500013014")
        SchoolMembership.objects.create(
            school=elsewhere, teacher=alien_manager, role_type=SchoolMembership.RoleType.MANAGER
        )
        alien_deputy = _user("وكيل آخر", "0500013015")
        SchoolMembership.objects.create(
            school=elsewhere, teacher=alien_deputy, role_type=SchoolMembership.RoleType.DEPUTY
        )
        delegation = Delegation.objects.create(
            school=elsewhere,
            delegator=alien_manager,
            delegate=alien_deputy,
            capabilities=[caps.VIEW_AUDIT_LOG],
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=1),
        )
        self._enter(self.manager)

        response = self.client.post(reverse("reports:delegation_revoke", args=[delegation.pk]))
        self.assertEqual(response.status_code, 404)

        delegation.refresh_from_db()
        self.assertIsNone(delegation.revoked_at)

    def test_a_scope_from_another_school_is_not_reachable(self):
        elsewhere = _school("مدرسة بعيدة", "screen-far")
        membership = SchoolMembership.objects.create(
            school=elsewhere,
            teacher=_user("وكيل بعيد", "0500013005"),
            role_type=SchoolMembership.RoleType.DEPUTY,
        )
        self._enter(self.manager)

        response = self.client.get(
            reverse("reports:staff_role_scope", args=[membership.pk])
        )
        self.assertEqual(response.status_code, 404)
