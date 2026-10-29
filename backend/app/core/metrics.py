"""Prometheus exposition for the few series operations actually needs (spec §10 "مقاييس أساسية").

Deliberately small:

* `route` is the FastAPI **path template** (`/api/v1/leads/{lead_id}`), never the raw URL — otherwise a
  crawler or a fuzzing run would mint unbounded series and take the scraper down with us.
* Worker-side counters (SLA sweep, intake) are *not* here: they live in another process and a gauge set
  by a worker would silently read as zero from the API. Those go to the worker logs until there is a
  pushgateway; pretending otherwise is worse than an honest gap (recorded in docs/STATUS.md).
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

_START = time.time()

REQUESTS = Counter(
    "broker_hub_http_requests_total",
    "HTTP requests handled by the API process.",
    ["method", "route", "status"],
)
DURATION = Histogram(
    "broker_hub_http_duration_seconds",
    "HTTP handler wall time.",
    ["method", "route"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
IN_FLIGHT = Gauge("broker_hub_http_in_flight_requests", "Requests currently inside the middleware.")
UPTIME = Gauge("broker_hub_process_uptime_seconds", "Seconds since this API process started.")


def observe(method: str, route: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    DURATION.labels(method=method, route=route).observe(seconds)


def exposition() -> bytes:
    UPTIME.set(time.time() - _START)
    return generate_latest()


__all__ = ["CONTENT_TYPE_LATEST", "IN_FLIGHT", "exposition", "observe"]
