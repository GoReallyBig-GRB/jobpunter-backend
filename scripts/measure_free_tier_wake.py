"""Measure cold-start latency of a deployed backend after idle gaps (secret-free).

For each idle gap it sleeps, then times GET /api/v1/health (process liveness only) and
GET /api/v1/readiness (touches the database, so it also shows database resume time).
It sends no credentials, changes no data, and writes only timings and status codes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _timed_get(base_url: str, path: str, timeout: float) -> dict[str, object]:
    started = time.monotonic()
    status = 0
    error: str | None = None
    try:
        with urlopen(Request(f"{base_url.rstrip('/')}{path}"), timeout=timeout) as response:
            status = response.status
            response.read(1024)
    except HTTPError as exc:
        status = exc.code
    except (OSError, URLError) as exc:
        error = type(exc).__name__
    return {
        "path": path,
        "status": status,
        "seconds": round(time.monotonic() - started, 3),
        "error": error,
    }


def measure(base_url: str, idle_seconds: list[int], rounds: int, timeout: float) -> list[dict]:
    results: list[dict] = []
    for round_number in range(1, rounds + 1):
        for idle in idle_seconds:
            if idle:
                print(f"round {round_number}: idle {idle}s", file=sys.stderr, flush=True)
                time.sleep(idle)
            health = _timed_get(base_url, "/api/v1/health", timeout)
            readiness = _timed_get(base_url, "/api/v1/readiness", timeout)
            results.append({
                "round": round_number,
                "idle_seconds": idle,
                "at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "health": health,
                "readiness": readiness,
            })
            print(
                f"idle={idle}s health={health['seconds']}s({health['status']}) "
                f"readiness={readiness['seconds']}s({readiness['status']})",
                file=sys.stderr, flush=True,
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--idle-seconds", type=int, nargs="+", default=[0, 600, 960, 1800],
                        help="idle gaps before each probe pair (0 = probe immediately)")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", default="free-tier-wake-evidence.json")
    args = parser.parse_args(argv)
    if args.rounds < 1 or any(value < 0 for value in args.idle_seconds):
        parser.error("--rounds must be >= 1 and --idle-seconds must be >= 0")
    results = measure(args.base_url, args.idle_seconds, args.rounds, args.timeout)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump({"base_url": args.base_url, "results": results}, handle, indent=2)
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
