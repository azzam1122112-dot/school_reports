# -*- coding: utf-8 -*-
"""سلامة القوالب وتغطية السجل.

**لماذا اختبار للقوالب؟** ``manage.py check`` لا يفتح قالباً واحداً. وقالبٌ فيه
خطأ نحوي أو ``{% url %}`` باسم غير موجود أو ``{% load %}`` ناقص لا يُكتشف إلا
حين يفتحه مستخدم — وربما بعد أسابيع إن كانت الشاشة نادرة الاستعمال.

هذا الاختبار يجمع كل قوالب المشروع ويجبر مُحرّك Django على تحليلها، فيلتقط:
- الأخطاء النحوية في وسوم القوالب،
- الوسوم والمرشّحات غير المحمّلة،
- ``{% url %}`` باسم مسار غير مسجَّل — وهو أكثر ما يتكرر عند إضافة شاشة.

ما لا يلتقطه: قيمةٌ ناقصة في السياق. ذلك شأن اختبارات الشاشات نفسها.
"""
from __future__ import annotations

import glob
import os

from django.template import TemplateSyntaxError
from django.template.loader import get_template
from django.test import TestCase


class TemplateIntegrityTests(TestCase):
    def test_every_template_compiles(self):
        broken = []
        for path in sorted(glob.glob("reports/templates/**/*.html", recursive=True)):
            name = os.path.relpath(path, "reports/templates").replace(os.sep, "/")
            try:
                get_template(name)
            except TemplateSyntaxError as exc:
                broken.append(f"{name}: {exc}")
            except Exception as exc:  # pragma: no cover — أي عطل تحميل آخر
                broken.append(f"{name}: {type(exc).__name__}: {exc}")

        self.assertEqual(broken, [], "قوالب لا تُحلَّل:\n" + "\n".join(broken))

    def test_no_template_references_an_unknown_url_name(self):
        """كل ``{% url 'reports:x' %}`` يشير إلى مسار مسجَّل.

        هذا أكثر ما ينكسر عند إضافة شاشة: يُكتب الرابط في القالب ويُنسى المسار،
        فتسقط كل صفحة تحوي الرابط — بما فيها صفحات لا علاقة لها بالشاشة الجديدة
        إن كان الرابط في ``base.html``.
        """
        import re

        from django.urls import NoReverseMatch, reverse

        pattern = re.compile(r"""\{%\s*url\s+['"]reports:([a-z0-9_]+)['"]""")
        unknown = set()

        for path in sorted(glob.glob("reports/templates/**/*.html", recursive=True)):
            text = open(path, encoding="utf-8").read()
            for name in pattern.findall(text):
                try:
                    reverse(f"reports:{name}")
                except NoReverseMatch as exc:
                    # المسارات ذات الوسائط تفشل بلا وسائط — وذلك ليس خطأً.
                    # الاسم المجهول وحده يذكر "not a valid view function".
                    if "not a valid view function or pattern name" in str(exc):
                        unknown.add(name)
                except Exception:
                    pass

        self.assertEqual(unknown, set(), f"أسماء مسارات غير مسجَّلة: {sorted(unknown)}")


class AuditCoverageCompletenessTests(TestCase):
    """كل كيان يحمل قراراً مشمولٌ بسجل الإجراءات.

    الكيانات التي بُنيت في المراحل المتأخرة كانت خارج التسجيل: خطوات اعتمادها
    محفوظة في ``ApprovalTransition``، لكن إنشاءها وتعديلها كان يمر بلا أثر.
    وهذا الاختبار يمنع تكرار السهو عند إضافة كيان جديد.
    """

    #: ما يُستثنى عمداً، ومعه سببه. إضافة اسم هنا قرارٌ يُكتب لا سهوٌ يمر.
    DELIBERATELY_UNAUDITED = {
        "AssignmentTarget": "نسبة الإنجاز تُحدَّث عشرات المرات؛ وحالاتها في ApprovalTransition",
        "MeetingAgendaItem": "بند في جدول أعمال — يتبع اجتماعه المسجَّل",
        "MeetingAttendee": "الحضور يتبع اجتماعه المسجَّل",
        "PlanGoal": "هدف في خطة — يتبع خطته المسجَّلة",
        "PlanTask": "مهمة في خطة — وتحويلها ينشئ تكليفاً مسجَّلاً",
    }

    def test_decision_carrying_models_are_audited(self):
        from reports.model_parts.signals import AUDITED_DELETE_MODELS, AUDITED_SAVE_MODELS

        expected = {
            "Assignment",
            "AssignmentEvidence",
            "Meeting",
            "MeetingMinutes",
            "Decision",
            "Plan",
            "Initiative",
            "Document",
            "CircularDraft",
            "StaffScope",
            "Delegation",
            "SchoolMembership",
            "Notification",
        }
        audited_save = {model.__name__ for model in AUDITED_SAVE_MODELS}
        audited_delete = {model.__name__ for model in AUDITED_DELETE_MODELS}

        missing_save = expected - audited_save
        missing_delete = expected - audited_delete

        self.assertEqual(missing_save, set(), f"خارج تسجيل الحفظ: {sorted(missing_save)}")
        self.assertEqual(missing_delete, set(), f"خارج تسجيل الحذف: {sorted(missing_delete)}")

    def test_every_audited_model_has_an_arabic_label(self):
        """سجلٌّ يعرض ``CircularDraft`` بدل «مسودة تعميم» سجلُّ نظام لا سجلُّ أعمال."""
        from reports.audit_labels import _MODELS
        from reports.model_parts.signals import AUDITED_SAVE_MODELS

        untranslated = [
            model.__name__
            for model in AUDITED_SAVE_MODELS
            if model.__name__ not in _MODELS
        ]
        self.assertEqual(untranslated, [], f"بلا تسمية عربية: {untranslated}")

    def test_the_school_resolver_covers_every_audited_model(self):
        """سجل بلا مدرسة لا يظهر في صفحة أي مدرسة — أي أنه سجل ضائع.

        نتحقق أن لكل نموذج مسجَّل طريقاً معروفاً إلى مدرسته: حقل ``school``
        مباشر، أو عبر ``department`` أو ``meeting`` أو ``target``.
        """
        from reports.model_parts.signals import AUDITED_SAVE_MODELS

        routes = ("school", "department", "meeting", "target")
        unreachable = []
        for model in AUDITED_SAVE_MODELS:
            if model.__name__ in {"School", "Teacher"}:
                continue  # School هي المدرسة، وTeacher عابر للمدارس.
            names = {field.name for field in model._meta.get_fields()}
            names |= set(vars(model))  # الخصائص المحسوبة مثل MeetingMinutes.school
            if not any(route in names for route in routes):
                unreachable.append(model.__name__)

        self.assertEqual(
            unreachable, [], f"بلا طريق إلى مدرستها في السجل: {unreachable}"
        )
