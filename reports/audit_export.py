# -*- coding: utf-8 -*-
"""Streaming export for already-authorized AuditLog querysets."""
from __future__ import annotations

import csv
import json

from django.http import StreamingHttpResponse
from django.utils import timezone

from .audit_labels import describe


class _Echo:
    def write(self, value):
        return value


def audit_csv_response(logs_qs, *, filename: str = "audit-log.csv") -> StreamingHttpResponse:
    """Stream the exact filtered queryset supplied by the permission-aware view."""
    writer = csv.writer(_Echo())

    def rows():
        yield "\ufeff"
        yield writer.writerow(
            [
                "الوقت",
                "المدرسة",
                "المستخدم",
                "الدور",
                "العملية",
                "نوع السجل",
                "السجل",
                "عنوان IP",
                "المتصفح",
                "التغييرات",
            ]
        )
        for log in logs_qs.iterator(chunk_size=500):
            ui = describe(log)
            local_timestamp = timezone.localtime(log.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            yield writer.writerow(
                [
                    local_timestamp,
                    getattr(getattr(log, "school", None), "name", "") or "",
                    log.actor_display,
                    log.actor_role or "",
                    log.get_action_display(),
                    ui.model_label,
                    log.object_repr or "",
                    log.ip_address or "",
                    log.user_agent or "",
                    json.dumps(log.changes, ensure_ascii=False, default=str) if log.changes else "",
                ]
            )

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
