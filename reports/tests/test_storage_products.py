"""كل مساحة تُباع بمنتجها، وتُضاف إلى حدّها هي.

كان في المنصة منتج توسعة واحد اسمه «زيادة مساحة الأرشيف» بينما أثره الفعلي
يضيف إلى مساحة عمل المدرسة — فالمدرسة التي امتلأ أرشيفها تدفع ولا يتحرك حدّها،
والتي امتلأت مساحة عملها تشتري شيئاً باسم لا يخصّها. وكانت رسالة امتلاء الأرشيف
تطلب «مساحة أرشيف إضافية» ولا يوجد ما يبيعها.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    ArchiveStorageOption,
    Payment,
    PlatformSettings,
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.services_archive import (
    school_archive_overview,
    school_storage_limit_bytes,
)

GB = 1024 * 1024 * 1024


class StorageProductTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 400
        settings_obj.free_storage_mb = 1024
        settings_obj.save(
            update_fields=["storage_mb_per_teacher", "free_storage_mb"]
        )

        self.school = School.objects.create(name="مدرسة المنتجات", code="products")
        self.plan = SubscriptionPlan.objects.create(
            name="سعة 25", price=1000, days_duration=365, max_teachers=25
        )
        SchoolSubscription.objects.create(school=self.school, plan=self.plan)
        self.school.refresh_from_db()

        self.manager = Teacher.objects.create_user(
            phone="500880001", name="مدير", password="prod-pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.superuser = Teacher.objects.create_superuser(
            phone="500880009", name="مدير النظام", password="root-pass"
        )

        self.work_option = ArchiveStorageOption.objects.create(
            bucket=ArchiveStorageOption.Bucket.WORK,
            storage_gb=20,
            price=Decimal("199.00"),
        )
        self.archive_option = ArchiveStorageOption.objects.create(
            bucket=ArchiveStorageOption.Bucket.ARCHIVE,
            storage_gb=30,
            price=Decimal("249.00"),
        )

    def _addon(self, *, limit_gb=50):
        return SchoolArchiveAddon.objects.create(
            school=self.school,
            is_enabled=True,
            start_date=timezone.localdate() - timedelta(days=1),
            end_date=timezone.localdate() + timedelta(days=300),
            storage_limit_gb=limit_gb,
        )

    def _login_manager(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def _approve(self, payment):
        from reports.views.subscriptions import _apply_payment_effects, _archive_pricing

        from django.db import transaction

        with transaction.atomic():
            return _apply_payment_effects(
                payment, timezone.localdate(), _archive_pricing()
            )

    # ------------------------------------------------- ما يستطيع مدير النظام تسعيره

    def test_the_operator_prices_each_space_separately(self):
        self.client.force_login(self.superuser)

        page = self.client.get(reverse("reports:platform_settings"))

        self.assertEqual(page.status_code, 200)
        # سعر الأرشفة السنوي ومساحتها المشمولة
        self.assertContains(page, "archive_addon_annual_price")
        self.assertContains(page, "archive_included_storage_gb")
        # حجم مساحة العمل المشتقة من سعة المعلمين، وحدّ المدرسة بلا اشتراك
        self.assertContains(page, "storage_mb_per_teacher")
        self.assertContains(page, "free_storage_mb")
        # وباقات التوسعة، ولكل باقة مساحتها المستهدفة
        self.assertContains(page, "storage_options-0-bucket")
        self.assertContains(page, "مساحة عمل المدرسة")
        self.assertContains(page, "مساحة الأرشفة السنوية")

    # ------------------------------------------- ما يستطيع مدير المدرسة شراءه

    def test_the_manager_sees_a_purchase_option_for_each_space(self):
        self._addon()
        self._login_manager()

        page = self.client.get(reverse("reports:my_subscription"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="archive_storage_option_id"')
        self.assertContains(page, 'name="archive_space_option_id"')
        self.assertContains(page, 'name="include_archive_storage"')
        self.assertContains(page, 'name="include_archive_space"')

    def test_buying_work_space_raises_the_work_limit_only(self):
        addon = self._addon(limit_gb=50)
        before_work = school_storage_limit_bytes(self.school)

        payment = Payment.objects.create(
            school=self.school,
            purpose=Payment.Purpose.WORK_STORAGE,
            archive_storage_gb=self.work_option.storage_gb,
            amount=self.work_option.price,
        )
        self._approve(payment)

        self.school.refresh_from_db()
        addon.refresh_from_db()
        self.assertEqual(
            school_storage_limit_bytes(self.school), before_work + 20 * GB
        )
        self.assertEqual(addon.storage_limit_gb, 50, "توسعة العمل مسّت حد الأرشفة")

    def test_buying_archive_space_raises_the_archive_limit_only(self):
        addon = self._addon(limit_gb=50)
        before_work = school_storage_limit_bytes(self.school)

        payment = Payment.objects.create(
            school=self.school,
            purpose=Payment.Purpose.ARCHIVE_SPACE,
            archive_storage_gb=self.archive_option.storage_gb,
            amount=self.archive_option.price,
        )
        self._approve(payment)

        addon.refresh_from_db()
        self.school.refresh_from_db()
        self.assertEqual(addon.storage_limit_gb, 80)
        self.assertEqual(school_archive_overview(self.school)["limit_bytes"], 80 * GB)
        self.assertEqual(
            school_storage_limit_bytes(self.school),
            before_work,
            "توسعة الأرشفة مسّت مساحة العمل",
        )

    def test_archive_space_is_refused_when_the_service_is_not_active(self):
        from reports.views.subscriptions import _ApprovalError, _archive_pricing
        from reports.views.subscriptions import _apply_payment_effects
        from django.db import transaction

        payment = Payment.objects.create(
            school=self.school,
            purpose=Payment.Purpose.ARCHIVE_SPACE,
            archive_storage_gb=30,
            amount=Decimal("249.00"),
        )

        with self.assertRaises(_ApprovalError):
            with transaction.atomic():
                _apply_payment_effects(
                    payment, timezone.localdate(), _archive_pricing()
                )

    def test_a_manager_cannot_request_archive_space_without_the_service(self):
        self._login_manager()

        self.client.post(
            reverse("reports:payment_create"),
            {
                "payment_kind": Payment.Purpose.ARCHIVE_SPACE,
                "archive_space_option_id": str(self.archive_option.pk),
                "payment_method": Payment.Method.BANK_TRANSFER,
            },
        )

        self.assertFalse(
            Payment.objects.filter(
                school=self.school, purpose=Payment.Purpose.ARCHIVE_SPACE
            ).exists(),
            "قُبل طلب مساحة أرشفة لمدرسة بلا خدمة أرشفة",
        )

    def test_an_option_cannot_be_spent_on_the_other_space(self):
        """اختيار باقة عملٍ في بند الأرشفة يجب أن يُرفض، لا أن يُطبّق."""
        self._addon()
        self._login_manager()

        self.client.post(
            reverse("reports:payment_create"),
            {
                "payment_kind": Payment.Purpose.ARCHIVE_SPACE,
                # باقة مساحة عمل في مكان باقة الأرشفة
                "archive_space_option_id": str(self.work_option.pk),
                "payment_method": Payment.Method.BANK_TRANSFER,
            },
        )

        self.assertFalse(
            Payment.objects.filter(
                school=self.school, purpose=Payment.Purpose.ARCHIVE_SPACE
            ).exists()
        )
