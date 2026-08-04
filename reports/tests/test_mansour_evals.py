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

        self.assertIn("39/39 (100.0%)", output.getvalue())
