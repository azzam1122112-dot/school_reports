from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from reports.mansour_assistant import ask_mansour, infer_public_audience
from reports.mansour_quality import (
    DIMENSION_LABELS,
    grade_answer,
    opening_diversity,
)


class Command(BaseCommand):
    help = (
        "قياس جودة نص ردود منصور كما يقرأها العميل. يعمل محليًا افتراضيًا "
        "على مسار الردود الاحتياطية، ويقيس ردود النموذج فعليًا مع --live."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--cases",
            default=str(Path(settings.BASE_DIR) / "reports" / "mansour_quality_cases.json"),
            help="مسار ملف حالات قياس الجودة.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help="استدعاء النموذج فعليًا لقياس الردود الحقيقية (يستهلك رصيد OpenAI).",
        )
        parser.add_argument(
            "--plans-from-db",
            action="store_true",
            help="استخدام باقات قاعدة البيانات بدل ترك قائمة الباقات فارغة.",
        )
        parser.add_argument(
            "--details",
            action="store_true",
            help="طباعة نص كل رد مع تفاصيل تقييمه.",
        )
        parser.add_argument("--minimum-score", type=float, default=0.0)
        parser.add_argument(
            "--only",
            default="",
            help="تصفية الحالات بمعرّف جزئي.",
        )

    def _load_cases(self, path: Path) -> list[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"تعذر قراءة حالات الجودة: {exc}") from exc
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list) or not cases:
            raise CommandError("ملف الجودة لا يحتوي قائمة cases صالحة.")
        return cases

    def _resolve_plans(self, use_db: bool) -> list[dict]:
        if not use_db:
            return []
        from reports.models import SubscriptionPlan

        return [
            {**plan, "price": f"{plan['price']:.2f}".rstrip("0").rstrip(".")}
            for plan in SubscriptionPlan.objects.filter(is_active=True)
            .order_by("price", "max_teachers", "days_duration", "id")
            .values("name", "price", "days_duration", "max_teachers")
        ]

    def handle(self, *args, **options):
        cases = self._load_cases(Path(options["cases"]))
        only = str(options["only"] or "").strip()
        if only:
            cases = [case for case in cases if only in str(case.get("id") or "")]
            if not cases:
                raise CommandError(f"لا توجد حالات تطابق: {only}")

        live = bool(options["live"])
        plans = self._resolve_plans(bool(options["plans_from_db"]))

        previous_enabled = getattr(settings, "MANSOUR_ASSISTANT_ENABLED", False)
        if not live:
            settings.MANSOUR_ASSISTANT_ENABLED = False
        elif not str(getattr(settings, "OPENAI_API_KEY", "") or "").strip():
            raise CommandError("الوضع الحي يتطلب ضبط OPENAI_API_KEY.")

        rows: list[tuple[dict, str, object]] = []
        try:
            for case in cases:
                question = str(case.get("question") or "").strip()
                configured = str(case.get("audience") or "general")
                audience = (
                    infer_public_audience(question) if configured == "auto" else configured
                )
                answer, _sources = ask_mansour(
                    question,
                    history=case.get("history"),
                    plans=plans,
                    audience=audience,
                )
                report = grade_answer(
                    answer,
                    audience=audience,
                    kind=str(case.get("kind") or "informational"),
                    expect_any=case.get("expect_any") or [],
                    forbid=case.get("forbid") or [],
                    allowed_prices=case.get("allowed_prices") or [],
                )
                rows.append((case, answer, report))
        finally:
            settings.MANSOUR_ASSISTANT_ENABLED = previous_enabled

        self._print_report(rows, live=live, details=bool(options["details"]))

        overall = sum(report.total for _case, _answer, report in rows) / len(rows)
        minimum = float(options["minimum_score"])
        if overall < minimum:
            raise CommandError(
                f"جودة الردود {overall:.1%} أقل من الحد المطلوب {minimum:.1%}."
            )

    def _print_report(self, rows, *, live: bool, details: bool) -> None:
        mode = "حي (OpenAI)" if live else "محلي (ردود احتياطية)"
        overall = sum(report.total for _case, _answer, report in rows) / len(rows)

        self.stdout.write(f"تقييم جودة ردود منصور — الوضع: {mode}")
        self.stdout.write(f"عدد الحالات: {len(rows)}")
        self.stdout.write(f"النتيجة الكلية: {overall:.1%}")
        self.stdout.write(
            f"تنوّع مطالع الردود: {opening_diversity([answer for _c, answer, _r in rows]):.1%}"
        )

        self.stdout.write("\nحسب المحور:")
        for key, label in DIMENSION_LABELS.items():
            scores = [
                report.dimension(key).score
                for _case, _answer, report in rows
                if report.dimension(key) and report.dimension(key).applicable
            ]
            if not scores:
                self.stdout.write(f"- {label}: لا ينطبق")
                continue
            average = sum(scores) / len(scores)
            style = self.style.SUCCESS if average >= 0.9 else self.style.WARNING
            self.stdout.write(style(f"- {label}: {average:.1%} ({len(scores)} حالة)"))

        self.stdout.write("\nحسب الفئة:")
        by_audience: dict[str, list[float]] = {}
        for case, _answer, report in rows:
            bucket = str(case.get("audience") or "general")
            by_audience.setdefault(bucket, []).append(report.total)
        for bucket, scores in sorted(by_audience.items()):
            self.stdout.write(
                f"- {bucket}: {sum(scores) / len(scores):.1%} ({len(scores)} حالة)"
            )

        ranked = sorted(rows, key=lambda row: row[2].total)
        self.stdout.write("\nأضعف الحالات:")
        for case, answer, report in ranked[:12]:
            if report.total >= 0.999:
                continue
            self.stdout.write(
                self.style.WARNING(f"- {case.get('id')}: {report.total:.1%}")
            )
            for note in report.notes:
                self.stdout.write(f"    · {note}")
            if details:
                for line in str(answer).splitlines():
                    self.stdout.write(f"      | {line}")

        if details:
            self.stdout.write("\nكل الردود:")
            for case, answer, report in rows:
                self.stdout.write(f"\n[{case.get('id')}] {report.total:.1%}")
                self.stdout.write(f"س: {case.get('question')}")
                for line in str(answer).splitlines():
                    self.stdout.write(f"ج: {line}")
