"""`/metrics` — the monitoring surface the runbook points at (spec §10).

The interesting properties are that the labels are route templates (not raw URLs) and that the endpoint
is never wide open outside development.
"""

from __future__ import annotations

from app.core.config import get_settings


async def test_metrics_count_requests_by_route_template(client) -> None:
    assert (await client.get("/healthz")).status_code == 200
    # a request to an unknown path may not mint a per-URL series (cardinality attack on the scraper)
    assert (await client.get("/api/v1/definitely-not-a-route")).status_code == 404
    body = (await client.get("/metrics")).text
    assert "broker_hub_http_requests_total" in body
    assert "broker_hub_http_duration_seconds_bucket" in body
    assert 'route="/healthz"' in body
    assert "/api/v1/definitely-not-a-route" not in body


async def test_metrics_need_the_configured_token(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "METRICS_TOKEN", "s3cret-observer", raising=False)
    anonymous = await client.get("/metrics")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "metrics_unauthorized"
    allowed = await client.get("/metrics", headers={"authorization": "Bearer s3cret-observer"})
    assert allowed.status_code == 200


async def test_metrics_are_closed_in_production_without_a_token(client, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "METRICS_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "DEBUG", False, raising=False)
    blocked = await client.get("/metrics")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "metrics_disabled"
