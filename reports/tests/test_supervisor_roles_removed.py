"""Regression guards for the removal of «مشرف المنصة» and «مشرف التقارير».

Two things must stay true after the removal:

1. No live code path still names either role. Migrations are excluded on
   purpose — they are the historical record of the schema and must keep
   referring to the columns they created and dropped.
2. The platform back office answers to the system owner (``is_superuser``)
   alone; ``is_staff`` no longer buys any of it.
"""

from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import School, SchoolMembership, Teacher


REPO_ROOT = Path(__file__).resolve().parents[2]

# Identifiers that only existed to serve the two removed roles.
FORBIDDEN_IDENTIFIERS = (
    "is_platform_admin",
    "PlatformAdminScope",
    "PlatformAdminRole",
    "PlatformAdminCreateForm",
    "PlatformAdminAccessMiddleware",
    "ReportViewerAccessMiddleware",
    "platform_can_access_school",
    "is_report_viewer_for_school",
    "IS_REPORT_VIEWER",
    "REPORT_VIEWER",
    "report_viewer",
    "AUDIENCE_PLATFORM_SUPERVISOR",
    "AUDIENCE_REPORT_SUPERVISOR",
    "platform_supervisor",
    "report_supervisor",
    # The words themselves: neither role may reappear in code, copy, CSS class
    # names, or assistant knowledge — in English or in Arabic.
    "supervisor",
    "Supervisor",
    "مشرف",
    "المشرف",
)

SCANNED_DIRS = ("reports", "config", "core", "static")
SCANNED_SUFFIXES = (".py", ".html", ".json", ".js", ".css", ".txt")


def _scanned_files():
    for directory in SCANNED_DIRS:
        root = REPO_ROOT / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SCANNED_SUFFIXES:
                continue
            parts = set(path.parts)
            if "migrations" in parts or "__pycache__" in parts:
                continue
            # This file names the identifiers it forbids.
            if path.name == Path(__file__).name:
                continue
            yield path


class SupervisorRolesFullyRemovedTests(TestCase):
    def test_no_live_source_file_mentions_a_removed_supervisor_role(self):
        offenders = []
        for path in _scanned_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for identifier in FORBIDDEN_IDENTIFIERS:
                if identifier in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {identifier}")

        self.assertEqual(
            offenders,
            [],
            "المشروع يجب أن يخلو من أي أثر للدورين المحذوفين:\n" + "\n".join(offenders),
        )

    def test_school_membership_offers_no_removed_supervisor_role(self):
        """الأدوار المحذوفة لا تعود — والقائمة تتوسّع بأدوار مقصودة.

        كان هذا الاختبار يجمّد القائمة على ``["teacher", "manager"]``، فحرس
        المقصد ومنع النمو معاً. والغرض من حذف الدورين السابقين لم يكن تجميد
        عدد الأدوار بل منع عودة **دورٍ بلا نطاق**؛ فالأدوار المضافة بعده
        (وكيل، موظف إداري) عضوياتٌ مُنطَقة بمدرسة بعينها، وهي عين ما كان
        ينقص المحذوفين.

        فيحرس الاختبار الآن أمرين: ألا يعود اسمٌ محذوف، وأن تبقى القائمة هي
        المجموعة المقصودة — فأي إضافة جديدة تمر من هنا بقرار لا سهواً.
        """
        values = [value for value, _label in SchoolMembership.RoleType.choices]

        for removed in ("report_viewer", "platform_admin", "supervisor"):
            self.assertNotIn(removed, values)

        self.assertEqual(
            sorted(values),
            sorted(["teacher", "manager", "deputy", "admin_staff"]),
        )

    def test_every_role_is_scoped_to_a_school(self):
        """لا دور يُقرأ من عَلَم على الحساب — كلها عضويات في مدرسة.

        هذا هو الدرس المستخلص من حذف الدورين السابقين، وهو ما يجب أن يُحرَس
        فعلاً: أي حقل منطقي على ``Teacher`` يمنح صلاحية إدارية يُعيد المشكلة.
        """
        boolean_flags = {
            field.name
            for field in Teacher._meta.get_fields()
            if getattr(field, "get_internal_type", lambda: "")() == "BooleanField"
        }
        # ما يجوز بقاؤه: أعلام Django القياسية وحالة الحساب.
        allowed = {"is_active", "is_staff", "is_superuser", "must_change_password"}
        suspicious = {
            name
            for name in boolean_flags - allowed
            if any(token in name for token in ("admin", "supervisor", "viewer", "manager"))
        }
        self.assertEqual(suspicious, set(), f"أعلام صلاحية على الحساب: {sorted(suspicious)}")

    def test_teacher_model_has_no_platform_admin_flag(self):
        field_names = {field.name for field in Teacher._meta.get_fields()}
        self.assertNotIn("is_platform_admin", field_names)
        self.assertNotIn("platform_scope", field_names)


@override_settings(ALLOWED_HOSTS=["testserver"])
class BackOfficeIsOwnerOnlyTests(TestCase):
    """Only ``is_superuser`` reaches the platform back office."""

    BACK_OFFICE_PAGES = (
        "reports:platform_admin_dashboard",
        "reports:platform_schools_directory",
        "reports:platform_settings",
        "reports:platform_payments_list",
        "reports:platform_subscriptions_list",
    )

    def setUp(self):
        self.school = School.objects.create(name="مدرسة الحراسة", code="back-office-guard")
        self.manager = Teacher.objects.create_user(
            phone="500880001",
            name="مدير المدرسة",
            password="guard-pass",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        # is_staff on its own must not open the back office.
        self.staff_user = Teacher.objects.create_user(
            phone="500880002",
            name="موظف لوحة",
            password="guard-pass",
            is_staff=True,
        )

    def _assert_all_closed(self, user):
        leaked = []
        for name in self.BACK_OFFICE_PAGES:
            response = self.client.get(reverse(name))
            if response.status_code == 200:
                leaked.append(name)
        self.assertEqual(leaked, [], f"back office leaked to {user.name}: {leaked}")

    def test_school_manager_cannot_reach_the_back_office(self):
        self.client.force_login(self.manager)
        self._assert_all_closed(self.manager)

    def test_staff_without_superuser_cannot_reach_the_back_office(self):
        self.client.force_login(self.staff_user)
        self._assert_all_closed(self.staff_user)

    def test_owner_reaches_the_back_office(self):
        owner = Teacher.objects.create_superuser(
            phone="500880003",
            name="مالك النظام",
            password="guard-pass",
        )
        self.client.force_login(owner)

        response = self.client.get(reverse("reports:platform_admin_dashboard"))

        self.assertEqual(response.status_code, 200)
