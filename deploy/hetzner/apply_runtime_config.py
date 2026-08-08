"""Apply runtime configuration to the server's env.production, safely.

Why this exists
---------------
``env.production`` lives only on the server: it holds live secrets, and the
deploy deliberately excludes it so a release can never clobber it. The cost of
that correct decision is **drift** — the repository's ``env.production.example``
is a template nobody diffs against the real file. A release once shipped with
Tamara still advertised and Moyasar still disabled: every test green, the image
correct, the site wrong. Nothing compared the two, because nothing could.

This script closes that gap without weakening the rule. It changes a **fixed,
named set of keys** and nothing else — it is not a generic "set any variable"
tool, because that would turn a config fix into a config-injection hole.

Guarantees
----------
- Secrets arrive on **stdin**, never in argv, so they stay out of the process
  list and the shell history.
- The file is backed up with a UTC timestamp before a single byte changes.
- Keys already present are rewritten in place; missing keys are appended. Order,
  comments and unrelated values survive untouched.
- The file is rewritten with ``0600`` permissions, matching the original intent.
- A value that fails validation aborts before anything is written.

Usage (from the deploy workflow, or by hand on the server)::

    printf '%s' "$MOYASAR_SECRET_KEY" | python3 apply_runtime_config.py \\
        --moyasar-enabled True --moyasar-environment live --tamara-enabled False
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENV_PATH = Path(
    os.environ.get("DEPLOY_PATH", "/opt/school_reports")
) / "deploy" / "hetzner" / "env.production"

BOOL_CHOICES = ("True", "False")

# Enough to undo a bad change; beyond that each one is another live-secret copy.
BACKUPS_TO_KEEP = 5


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--tamara-enabled", choices=BOOL_CHOICES)
    parser.add_argument("--moyasar-enabled", choices=BOOL_CHOICES)
    parser.add_argument("--moyasar-environment", choices=("live", "test"))
    parser.add_argument("--pdf-offload-enabled", choices=BOOL_CHOICES)
    parser.add_argument("--celery-media-concurrency", type=int)
    parser.add_argument(
        "--moyasar-key-from-stdin",
        action="store_true",
        help="Read MOYASAR_SECRET_KEY from stdin. An empty read leaves it unchanged.",
    )
    return parser.parse_args()


def _collect(args: argparse.Namespace) -> dict[str, str]:
    """Build the key set, validating every value before anything is written."""
    values: dict[str, str] = {}

    if args.tamara_enabled:
        values["TAMARA_ENABLED"] = args.tamara_enabled
    if args.moyasar_enabled:
        values["MOYASAR_ENABLED"] = args.moyasar_enabled
    if args.moyasar_environment:
        values["MOYASAR_ENVIRONMENT"] = args.moyasar_environment
    if args.pdf_offload_enabled:
        values["PDF_OFFLOAD_ENABLED"] = args.pdf_offload_enabled
    if args.celery_media_concurrency is not None:
        if not 1 <= args.celery_media_concurrency <= 8:
            raise SystemExit("CELERY_MEDIA_CONCURRENCY must be between 1 and 8.")
        values["CELERY_MEDIA_CONCURRENCY"] = str(args.celery_media_concurrency)

    if args.moyasar_key_from_stdin:
        key = sys.stdin.read().strip()
        if key:
            # The same rule settings.py enforces at boot — but caught here, before
            # the file is written, so a wrong key never reaches a restart.
            if not re.fullmatch(r"sk_(live|test)_[A-Za-z0-9]{8,}", key):
                raise SystemExit("MOYASAR_SECRET_KEY does not look like an sk_live_/sk_test_ key.")
            env = values.get("MOYASAR_ENVIRONMENT")
            expected = "sk_live_" if env == "live" else "sk_test_"
            if env and not key.startswith(expected):
                raise SystemExit(
                    f"MOYASAR_SECRET_KEY does not match MOYASAR_ENVIRONMENT={env}."
                )
            values["MOYASAR_SECRET_KEY"] = key

    if not values:
        raise SystemExit("Nothing to apply — pass at least one option.")
    return values


def _prune_backups(path: Path, keep: int = BACKUPS_TO_KEEP) -> list[str]:
    """Keep the most recent backups and shred the rest.

    Every backup is a **complete copy of the live secrets**. Letting them pile up
    turns one 0600 file into a growing directory of them: more copies to leak,
    more to forget when rotating a key. A handful is enough to undo a bad change;
    beyond that they are liability, not safety.

    The timestamp is a sortable UTC stamp, so lexical order is chronological.
    """
    backups = sorted(path.parent.glob(f"{path.name}.bak.*"))
    removed: list[str] = []
    for old in backups[:-keep] if keep else backups:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            pass
    return removed


def _rewrite(path: Path, values: dict[str, str]) -> list[str]:
    original = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(values)
    changed: list[str] = []
    output: list[str] = []

    for line in original:
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            new_value = remaining.pop(key)
            if match.group(2) != new_value:
                changed.append(key)
            output.append(f"{key}={new_value}")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")
        changed.append(key)

    # Write to a sibling temp file, then rename over the original. ``rename``
    # within a directory is atomic on POSIX, so a reader either sees the whole
    # old file or the whole new one — never a half-written env that would stop
    # every container from booting. Writing in place left exactly that window;
    # the backup could undo it, but only after someone noticed the outage.
    #
    # 0600 is set on the temp file *before* the content lands in it, so the
    # secrets are never briefly world-readable.
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    path.chmod(0o600)
    return changed


def main() -> None:
    args = _parse_args()
    path: Path = args.env_path
    if not path.is_file():
        raise SystemExit(f"{path} not found — run this on the server.")

    values = _collect(args)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.{timestamp}")
    shutil.copy2(path, backup)

    changed = _rewrite(path, values)

    # Names only. Printing a value here would put a live payment key into the
    # workflow log, which is exactly what reading it from stdin was for.
    pruned = _prune_backups(path)

    print(f"[config] backup: {backup.name}")
    if pruned:
        print(f"[config] pruned {len(pruned)} older backup(s)")
    print(f"[config] updated: {', '.join(sorted(changed)) or 'nothing (already current)'}")


if __name__ == "__main__":
    main()
