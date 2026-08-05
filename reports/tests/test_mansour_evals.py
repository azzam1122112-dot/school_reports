import re
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase


class MansourEvaluationSuiteTests(SimpleTestCase):
    def test_curated_role_and_retrieval_evals_pass(self):
        output = StringIO()

        call_command(
            "evaluate_mansour",
            minimum_score=1.0,
            stdout=output,
        )

        # ما يحرسه هذا الاختبار هو نجاح كل حالة، لا عددها. تثبيت العدد كان يُسقطه
        # عند أي إضافة أو حذف مشروع لحالة تقييم — وهو فشل لسبب لا يخصّه.
        report = output.getvalue()
        match = re.search(r"Mansour eval: (\d+)/(\d+) \(([\d.]+)%\)", report)
        self.assertIsNotNone(match, f"تعذّر قراءة ملخّص التقييم من: {report}")

        passed, total, percent = int(match[1]), int(match[2]), float(match[3])
        self.assertGreater(total, 0, "لا توجد حالات تقييم مُحمّلة.")
        self.assertEqual(passed, total, f"حالات تقييم فاشلة: {total - passed} من {total}")
        self.assertEqual(percent, 100.0)
