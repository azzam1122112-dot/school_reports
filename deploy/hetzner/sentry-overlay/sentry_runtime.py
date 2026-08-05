"""Minimal production Sentry bootstrap for the currently deployed image."""
from __future__ import annotations

import os


def configure_sentry() -> None:
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return

    import sentry_sdk

    try:
        traces_sample_rate = min(
            1.0,
            max(0.0, float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05") or "0.05")),
        )
    except (TypeError, ValueError):
        traces_sample_rate = 0.05

    sentry_sdk.init(
        dsn=dsn,
        environment=(os.getenv("ENV") or "production").strip().lower(),
        release=(os.getenv("SENTRY_RELEASE") or "").strip() or None,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
    )
