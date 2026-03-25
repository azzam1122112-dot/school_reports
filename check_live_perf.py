from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests


@dataclass
class RouteStats:
    path: str
    ok: int
    fail: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float
    rps: float


class LivePerfRunner:
    def __init__(
        self,
        base_url: str,
        login_path: str,
        username: str,
        password: str,
        username_field: str,
        password_field: str,
        active_school_id: Optional[int],
        timeout_s: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.login_path = login_path
        self.username = username
        self.password = password
        self.username_field = username_field
        self.password_field = password_field
        self.active_school_id = active_school_id
        self.timeout_s = timeout_s
        self._lock = threading.Lock()

    def _full_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def build_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": "school-reports-live-perf/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

        login_url = self._full_url(self.login_path)
        # Prime cookies/CSRF
        s.get(login_url, timeout=self.timeout_s)
        csrf = s.cookies.get("csrftoken", "")

        payload = {
            self.username_field: self.username,
            self.password_field: self.password,
        }
        if csrf:
            payload["csrfmiddlewaretoken"] = csrf
            s.headers.update({"Referer": login_url, "X-CSRFToken": csrf})
        resp = s.post(login_url, data=payload, timeout=self.timeout_s, allow_redirects=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"Login failed with status {resp.status_code}")
        if "sessionid" not in s.cookies:
            raise RuntimeError("Login failed: no authenticated session cookie returned")
        final_url = str(getattr(resp, "url", "") or "")
        if final_url.rstrip("/").endswith(self.login_path.rstrip("/")):
            raise RuntimeError("Login failed: still on login page after POST")

        if self.active_school_id is not None:
            cookie_name = "active_school_id"
            s.cookies.set(cookie_name, str(int(self.active_school_id)))

        return s

    def _single_request(self, s: requests.Session, path: str) -> Tuple[bool, float, int]:
        url = self._full_url(path)
        t0 = time.perf_counter()
        try:
            r = s.get(url, timeout=self.timeout_s, allow_redirects=True)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            ok = 200 <= r.status_code < 400
            return ok, dt_ms, r.status_code
        except Exception:
            dt_ms = (time.perf_counter() - t0) * 1000.0
            return False, dt_ms, 0

    @staticmethod
    def _percentile(vals: List[float], q: float) -> float:
        if not vals:
            return 0.0
        if len(vals) == 1:
            return vals[0]
        idx = (len(vals) - 1) * q
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return vals[int(idx)]
        return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)

    def run_route(self, s: requests.Session, path: str, requests_n: int, concurrency: int, warmup: int) -> RouteStats:

        for _ in range(max(0, warmup)):
            self._single_request(s, path)

        latencies: List[float] = []
        ok_n = 0
        fail_n = 0

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
            futures = [ex.submit(self._single_request, s, path) for _ in range(max(1, requests_n))]
            for f in as_completed(futures):
                ok, dt_ms, _status = f.result()
                latencies.append(dt_ms)
                if ok:
                    ok_n += 1
                else:
                    fail_n += 1
        total_s = max(1e-9, time.perf_counter() - t0)

        latencies.sort()
        avg_ms = statistics.fmean(latencies) if latencies else 0.0
        p50_ms = self._percentile(latencies, 0.50)
        p95_ms = self._percentile(latencies, 0.95)
        p99_ms = self._percentile(latencies, 0.99)
        min_ms = latencies[0] if latencies else 0.0
        max_ms = latencies[-1] if latencies else 0.0
        rps = (ok_n + fail_n) / total_s

        return RouteStats(
            path=path,
            ok=ok_n,
            fail=fail_n,
            p50_ms=p50_ms,
            p95_ms=p95_ms,
            p99_ms=p99_ms,
            avg_ms=avg_ms,
            min_ms=min_ms,
            max_ms=max_ms,
            rps=rps,
        )


def render_table(rows: List[RouteStats], title: str) -> str:
    header = (
        f"\n{title}\n"
        f"{'Route':35} {'OK':>6} {'Fail':>6} {'RPS':>8} {'P50':>8} {'P95':>8} {'P99':>8} {'Avg':>8} {'Max':>8}\n"
        + "-" * 105
    )
    lines = [header]
    for r in rows:
        lines.append(
            f"{r.path[:35]:35} {r.ok:6d} {r.fail:6d} {r.rps:8.2f} {r.p50_ms:8.1f} {r.p95_ms:8.1f} {r.p99_ms:8.1f} {r.avg_ms:8.1f} {r.max_ms:8.1f}"
        )
    return "\n".join(lines)


def compare(before: List[RouteStats], after: List[RouteStats]) -> str:
    by_path_before: Dict[str, RouteStats] = {x.path: x for x in before}
    by_path_after: Dict[str, RouteStats] = {x.path: x for x in after}
    paths = [p for p in by_path_before if p in by_path_after]

    lines = [
        "\nBefore/After Delta (negative latency is better)",
        f"{'Route':35} {'P95 Δms':>10} {'RPS Δ':>10} {'Err Δ':>10}",
        "-" * 72,
    ]
    for p in paths:
        b = by_path_before[p]
        a = by_path_after[p]
        d_p95 = a.p95_ms - b.p95_ms
        d_rps = a.rps - b.rps
        d_err = (a.fail - b.fail)
        lines.append(f"{p[:35]:35} {d_p95:10.1f} {d_rps:10.2f} {d_err:10d}")
    return "\n".join(lines)


def run_phase(runner: LivePerfRunner, routes: List[str], requests_n: int, concurrency: int, warmup: int) -> List[RouteStats]:
    out: List[RouteStats] = []
    session = runner.build_session()
    for route in routes:
        out.append(runner.run_route(session, route, requests_n=requests_n, concurrency=concurrency, warmup=warmup))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Short live benchmark for critical routes (RPS/Latency/Errors)")
    p.add_argument("--base-url", required=True)
    p.add_argument("--login-path", default="/login/")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--username-field", default="phone")
    p.add_argument("--password-field", default="password")
    p.add_argument("--active-school-id", type=int, default=None)
    p.add_argument("--requests", type=int, default=24)
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--timeout", type=int, default=25)
    p.add_argument(
        "--routes",
        nargs="+",
        default=[
            "/home/",
            "/reports/admin/",
            "/staff/departments/",
            "/reports/my/",
            "/notifications/mine/",
        ],
    )
    p.add_argument(
        "--before-json",
        default=None,
        help="Optional JSON metrics file from an older run to print before/after deltas",
    )
    p.add_argument("--out-json", default="live_perf_after.json")
    args = p.parse_args()

    runner = LivePerfRunner(
        base_url=args.base_url,
        login_path=args.login_path,
        username=args.username,
        password=args.password,
        username_field=args.username_field,
        password_field=args.password_field,
        active_school_id=args.active_school_id,
        timeout_s=args.timeout,
    )

    after_rows = run_phase(
        runner=runner,
        routes=args.routes,
        requests_n=args.requests,
        concurrency=args.concurrency,
        warmup=args.warmup,
    )

    print(render_table(after_rows, "AFTER (current build)"))

    payload = {
        "base_url": args.base_url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "routes": [r.__dict__ for r in after_rows],
        "ts_epoch": time.time(),
    }
    with open(args.out_json, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    if args.before_json:
        with open(args.before_json, "r", encoding="utf-8") as fp:
            prev = json.load(fp)
        before_rows = [RouteStats(**x) for x in prev.get("routes", [])]
        print(compare(before_rows, after_rows))


if __name__ == "__main__":
    main()
