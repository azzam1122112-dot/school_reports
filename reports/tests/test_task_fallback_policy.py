"""What happens to background work when the Celery broker is unreachable.

The rule: work whose absence loses data still runs inline, while work that only
improves an already-correct result is dropped rather than allowed to block web
requests and cascade a broker outage into a site-wide slowdown.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from reports.utils import run_task_safe


class _BrokerDown(Exception):
    pass


class FakeTask:
    """Stands in for a Celery task whose enqueue fails."""

    __name__ = "fake_task"

    def __init__(self):
        self.inline_calls = []

    def apply_async(self, args=None, kwargs=None, headers=None):
        raise _BrokerDown("broker unreachable")

    def apply(self, args=None, kwargs=None, throw=False):
        self.inline_calls.append((tuple(args or ()), dict(kwargs or {})))


@override_settings(DEBUG=False)
class TaskFallbackPolicyTests(TestCase):
    def _run(self, task, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            run_task_safe(task, 42, **kwargs)

    def test_critical_task_still_runs_inline_when_the_broker_is_down(self):
        task = FakeTask()

        self._run(task)

        self.assertEqual(task.inline_calls, [((42,), {})])

    def test_optional_task_is_dropped_instead_of_blocking_the_request(self):
        task = FakeTask()

        self._run(task, inline_fallback=False)

        self.assertEqual(task.inline_calls, [])

    def test_dropping_a_task_is_recorded_for_alerting(self):
        task = FakeTask()

        with patch("core.opmetrics.increment") as increment:
            self._run(task, inline_fallback=False)

        recorded = {call.args[0] for call in increment.call_args_list}
        self.assertIn("celery.enqueue.failed", recorded)
        self.assertIn("celery.task.dropped", recorded)

    def test_broker_failure_is_recorded_even_when_the_task_runs_inline(self):
        task = FakeTask()

        with patch("core.opmetrics.increment") as increment:
            self._run(task)

        recorded = {call.args[0] for call in increment.call_args_list}
        self.assertIn("celery.enqueue.failed", recorded)

    def test_image_compression_does_not_run_inline_on_report_save(self):
        """Compression is an optimisation — the uploaded images stay valid
        without it, so a broker outage must not push Pillow work into the
        request/response cycle."""
        import inspect

        from reports.model_parts import signals

        source = inspect.getsource(signals.trigger_report_background_tasks)

        self.assertIn("inline_fallback=False", source)
