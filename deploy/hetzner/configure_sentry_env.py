"""Safely update the production environment with a DSN read from stdin."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sys


ENV_PATH = Path("/opt/school-reports/deploy/hetzner/env.production")
VALUES = {
    "APP_IMAGE": "school-reports:sentry-20260802",
    "SENTRY_RELEASE": "school-reports-sentry-20260802",
    "SENTRY_TRACES_SAMPLE_RATE": "0.05",
}


def main() -> None:
    dsn = sys.stdin.read().strip()
    if not re.fullmatch(r"https://[^@\s]+@[^/\s]*sentry\.io/\d+", dsn):
        raise SystemExit("Invalid Sentry DSN")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = ENV_PATH.with_name(f"env.production.bak.sentry-{timestamp}")
    shutil.copy2(ENV_PATH, backup_path)

    values = {**VALUES, "SENTRY_DSN": dsn}
    output: list[str] = []
    seen: set[str] = set()
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        key = ""
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)

    output.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)
    print("sentry_environment_updated=yes")


if __name__ == "__main__":
    main()
