from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reports.mansour_assistant import (
    _default_knowledge,
    _detect_customer_intent,
    _is_role_overview_question,
    infer_public_audience,
    select_knowledge,
)


class Command(BaseCommand):
    help = "تشغيل تقييم محلي لمسارات منصور دون إرسال أي بيانات إلى OpenAI."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cases",
            default=str(Path(settings.BASE_DIR) / "reports" / "mansour_eval_cases.json"),
            help="مسار ملف حالات التقييم بصيغة JSON.",
        )
        parser.add_argument("--top-k", type=int, default=3)
        parser.add_argument("--minimum-score", type=float, default=0.95)

    def handle(self, *args, **options):
        path = Path(options["cases"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"تعذر قراءة حالات التقييم: {exc}") from exc

        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list) or not cases:
            raise CommandError("ملف التقييم لا يحتوي قائمة cases صالحة.")

        top_k = max(1, int(options["top_k"]))
        failures: list[str] = []
        role_totals: dict[str, list[int]] = {}

        for case in cases:
            case_id = str(case.get("id") or "unnamed")
            question = str(case.get("question") or "").strip()
            configured_audience = str(case.get("audience") or "general")
            audience = (
                infer_public_audience(question)
                if configured_audience == "auto"
                else configured_audience
            )
            selected = select_knowledge(question, audience=audience, limit=top_k)
            # A generic "what can I do?" needs the default journey, while an
            # explicitly named role such as deputy has a dedicated article.
            if _is_role_overview_question(question) and not any(
                item.slug == "school-role-model" for item in selected
            ):
                selected = _default_knowledge(audience, limit=top_k)
            selected_slugs = [item.slug for item in selected]
            expected_slugs = [str(value) for value in case.get("expected_slugs") or []]
            expected_intent = str(case.get("expected_intent") or "")
            expected_audience = str(case.get("expected_audience") or "")

            checks = []
            if expected_slugs:
                checks.append(bool(set(expected_slugs) & set(selected_slugs)))
            if expected_intent:
                checks.append(_detect_customer_intent(question) == expected_intent)
            if expected_audience:
                checks.append(audience == expected_audience)
            passed = bool(checks) and all(checks)

            role_bucket = expected_audience or audience
            role_totals.setdefault(role_bucket, [0, 0])
            role_totals[role_bucket][1] += 1
            if passed:
                role_totals[role_bucket][0] += 1
            else:
                failures.append(
                    f"{case_id}: audience={audience}, intent={_detect_customer_intent(question)}, "
                    f"selected={selected_slugs}, expected={expected_slugs or '-'}"
                )

        passed_count = len(cases) - len(failures)
        score = passed_count / len(cases)
        self.stdout.write(f"Mansour eval: {passed_count}/{len(cases)} ({score:.1%})")
        for role, (role_passed, role_count) in sorted(role_totals.items()):
            self.stdout.write(f"- {role}: {role_passed}/{role_count}")
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"FAIL {failure}"))

        if score < float(options["minimum_score"]):
            raise CommandError(
                f"نتيجة التقييم {score:.1%} أقل من الحد المطلوب "
                f"{float(options['minimum_score']):.1%}."
            )
        self.stdout.write(self.style.SUCCESS("نجح تقييم منصور المحلي."))
