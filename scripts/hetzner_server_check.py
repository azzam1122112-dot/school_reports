#!/usr/bin/env python3
"""Verify the production server through the Hetzner Cloud API.

The Hetzner console at console.hetzner.com is a JavaScript app behind your
personal login and 2FA — it cannot be inspected by a script, and handing over
those credentials is not the way to automate anything. The Cloud API is the
supported path: create a token scoped to the project, give it **Read** only, and
this script can report the server's real state without ever touching your
password.

Read-only by construction: every request here is a GET, and the script has no
code path that writes, reboots, resizes or deletes anything.

    # Hetzner Console -> Security -> API tokens -> Generate (permission: Read)
    export HETZNER_API_TOKEN=...            # PowerShell: $env:HETZNER_API_TOKEN='...'
    python scripts/hetzner_server_check.py 155662703

The token is read from the environment on purpose — passing it as an argument
would leave it in your shell history and in the process list. Revoke it in the
console when you are done.

Exit code 0 if nothing is wrong, 1 if any check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_ROOT = "https://api.hetzner.cloud/v1"

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"

results: list[tuple[str, str, str]] = []


def record(level: str, title: str, detail: str = "") -> None:
    results.append((level, title, detail))


def get(path: str, token: str, params: dict | None = None) -> dict:
    url = f"{API_ROOT}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        method="GET",  # never anything else
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "tawtheeq-server-check/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def human_bytes(value: float) -> str:
    step = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024:
            return f"{step:.1f}{unit}"
        step /= 1024
    return f"{step:.1f}PB"


def check_server(server: dict) -> None:
    status = str(server.get("status") or "")
    if status == "running":
        record(PASS, f"Server '{server.get('name')}' is running")
    else:
        record(FAIL, f"Server status is '{status}'", "Expected 'running'.")

    server_type = server.get("server_type") or {}
    datacenter = server.get("datacenter") or {}
    location = (datacenter.get("location") or {}) if datacenter else {}
    where = " / ".join(
        part for part in (datacenter.get("name"), location.get("city")) if part
    )
    # ASCII separator: Windows consoles mangle an em dash in this output.
    record(
        INFO,
        f"{server_type.get('name', '?')} | "
        f"{server_type.get('cores', '?')} vCPU, "
        f"{server_type.get('memory', '?')}GB RAM, "
        f"{server_type.get('disk', '?')}GB disk",
        where,
    )

    public_net = server.get("public_net") or {}
    ipv4 = (public_net.get("ipv4") or {}).get("ip")
    if ipv4:
        record(INFO, f"Public IPv4: {ipv4}")

    # A server left in rescue or with an ISO attached is mid-incident, not serving.
    if server.get("rescue_enabled"):
        record(FAIL, "Rescue mode is enabled", "The server is not booted normally.")
    else:
        record(PASS, "Not in rescue mode")

    if server.get("iso"):
        record(WARN, "An ISO is still attached", str((server.get("iso") or {}).get("name")))

    if server.get("locked"):
        record(WARN, "Server is locked", "An action is in progress.")

    protection = server.get("protection") or {}
    if protection.get("delete"):
        record(PASS, "Delete protection is on")
    else:
        record(
            WARN,
            "Delete protection is off",
            "One mis-click in the console can destroy the production server.",
        )

    backup_window = server.get("backup_window")
    if backup_window:
        record(PASS, f"Hetzner backups enabled (window {backup_window})")
    else:
        record(
            WARN,
            "Hetzner-level backups are not enabled",
            "The project's own encrypted dumps still run; this is the extra "
            "snapshot layer that survives a lost filesystem.",
        )

    volumes = server.get("volumes") or []
    record(INFO, f"{len(volumes)} attached volume(s)")


def check_firewalls(server: dict) -> None:
    firewalls = server.get("public_net", {}).get("firewalls") or []
    applied = [f for f in firewalls if str(f.get("status")) == "applied"]
    if applied:
        record(PASS, f"{len(applied)} firewall(s) applied")
    else:
        record(
            WARN,
            "No Hetzner firewall applied",
            "The host relies entirely on its own rules; only 80/443 should be exposed.",
        )


def check_metrics(server_id: int, token: str) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    try:
        payload = get(
            f"/servers/{server_id}/metrics",
            token,
            {
                "type": "cpu,disk,network",
                "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
    except Exception as exc:
        record(WARN, "Could not read metrics", str(exc)[:160])
        return

    series = (payload.get("metrics") or {}).get("time_series") or {}

    def latest(name: str):
        values = (series.get(name) or {}).get("values") or []
        if not values:
            return None
        try:
            return float(values[-1][1])
        except (TypeError, ValueError, IndexError):
            return None

    def peak(name: str):
        values = (series.get(name) or {}).get("values") or []
        numbers = []
        for entry in values:
            try:
                numbers.append(float(entry[1]))
            except (TypeError, ValueError, IndexError):
                continue
        return max(numbers) if numbers else None

    cpu_now, cpu_peak = latest("cpu"), peak("cpu")
    if cpu_now is not None:
        level = FAIL if (cpu_peak or 0) >= 95 else (WARN if (cpu_peak or 0) >= 80 else PASS)
        record(level, f"CPU now {cpu_now:.1f}%, peak (1h) {cpu_peak:.1f}%")
    else:
        record(INFO, "No CPU samples in the last hour")

    for label, key in (("disk read", "disk.0.iops.read"), ("disk write", "disk.0.iops.write")):
        value = latest(key)
        if value is not None:
            record(INFO, f"{label}: {value:.0f} IOPS")

    for label, key in (
        ("network in", "network.0.pps.in"),
        ("network out", "network.0.pps.out"),
    ):
        value = latest(key)
        if value is not None:
            record(INFO, f"{label}: {value:.0f} pps")

    for label, key in (
        ("bandwidth in", "network.0.bandwidth.in"),
        ("bandwidth out", "network.0.bandwidth.out"),
    ):
        value = latest(key)
        if value is not None:
            record(INFO, f"{label}: {human_bytes(value)}/s")


def check_recent_actions(server_id: int, token: str) -> None:
    try:
        payload = get(f"/servers/{server_id}/actions", token, {"per_page": "25"})
    except Exception as exc:
        record(WARN, "Could not read recent actions", str(exc)[:160])
        return

    actions = sorted(
        payload.get("actions") or [],
        key=lambda a: str(a.get("started") or ""),
        reverse=True,
    )
    if not actions:
        record(INFO, "No recent server actions")
        return

    failed = [a for a in actions if str(a.get("status")) == "error"]
    if failed:
        record(
            FAIL,
            f"{len(failed)} recent action(s) failed",
            ", ".join(str(a.get("command")) for a in failed[:3]),
        )
    else:
        record(PASS, "No failed actions recently")

    for action in actions[:3]:
        record(
            INFO,
            f"action: {action.get('command')} — {action.get('status')}",
            str(action.get("finished") or action.get("started") or ""),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("server_id", type=int, help="Hetzner server id, e.g. 155662703")
    args = parser.parse_args()

    token = (os.getenv("HETZNER_API_TOKEN") or "").strip()
    if not token:
        print(
            "HETZNER_API_TOKEN is not set.\n\n"
            "Create one in the Hetzner Console: Security -> API tokens -> Generate,\n"
            "with permission 'Read'. Then:\n"
            "    export HETZNER_API_TOKEN=...        (PowerShell: $env:HETZNER_API_TOKEN='...')\n\n"
            "Revoke the token in the console once you are finished.",
            file=sys.stderr,
        )
        return 2

    print("=== Hetzner server check (read-only) ===")
    try:
        payload = get(f"/servers/{args.server_id}", token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            print("The API token was rejected. Check it, or that it belongs to this project.", file=sys.stderr)
        elif exc.code == 404:
            print(f"Server {args.server_id} is not visible to this token's project.", file=sys.stderr)
        else:
            print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Could not reach the Hetzner API: {exc}", file=sys.stderr)
        return 2

    server = payload.get("server") or {}
    check_server(server)
    check_firewalls(server)
    check_metrics(args.server_id, token)
    check_recent_actions(args.server_id, token)

    print()
    for level, title, detail in results:
        print(f"  {level:<5} {title}")
        if detail:
            print(f"        {detail}")

    failures = sum(1 for level, _, _ in results if level == FAIL)
    warnings = sum(1 for level, _, _ in results if level == WARN)
    print()
    verdict = "PROBLEMS FOUND" if failures else "HEALTHY"
    print(f"{verdict}: {failures} failure(s), {warnings} warning(s)")
    print(
        "\nThis covers the machine only. For the application inside it, run:\n"
        "  docker compose -f compose.hetzner.yaml exec web python manage.py production_preflight"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
