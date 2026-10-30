"""The inbound delivery row must say what happened, not only that nothing exploded (item 4).

During the WhatsApp debugging, every delivery answered `200` with a green `webhook_events` row — and the
inbox stayed empty. Four very different things produced that identical picture: a duplicate retry, a message
appended to an open thread, a Lead match (which deliberately creates no request), and a brand-new ticket.
The operator was left to guess, which is what this test makes impossible.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

from app.core.config import get_settings
from app.models.integrations import WebhookEvent
from sqlalchemy import select

MANAGER = "manager@mantiq.test"


def _payload(external_id: str, mobile: str) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": mobile, "profile": {"name": "مرسل"}}],
                                "messages": [{"id": external_id, "from": mobile, "type": "text", "text": {"body": "استفسار"}}],
                            }
                        }
                    ]
                }
            ],
        }
    ).encode()


def _signature(raw: bytes) -> str:
    digest = hmac.new(get_settings().WHATSAPP_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _post(client: Any, raw: bytes) -> dict[str, Any]:
    response = await client.post("/api/v1/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": _signature(raw)})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["results"][0]  # the envelope reports one result per message in the batch


async def _message_row(session: Any, external_id: str) -> WebhookEvent:
    row = (
        (
            await session.execute(
                select(WebhookEvent)
                .where(WebhookEvent.provider == "whatsapp", WebhookEvent.event_type == "message", WebhookEvent.external_id == external_id)
                .order_by(WebhookEvent.occurred_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert row is not None, "every ingested message must leave a row"
    return row


async def test_a_brand_new_ticket_is_recorded_as_created_with_its_match(client: Any, session: Any) -> None:
    external_id = f"wamid.r17-created-{uuid.uuid4().hex[:8]}"
    mobile = "01099880001"
    result = await _post(client, _payload(external_id, mobile))

    row = await _message_row(session, external_id)
    assert result["outcome"] == "created"
    assert row.detail is not None
    assert "outcome=created" in row.detail
    # the match kind is part of the answer: `insured` vs `lead` vs `unknown` decide what the operator checks next
    assert "matched=" in row.detail
    assert f"matched={result['matched_kind']}" in row.detail


async def test_a_duplicate_retry_says_so(client: Any, session: Any) -> None:
    external_id = f"wamid.r17-dupe-{uuid.uuid4().hex[:8]}"
    raw = _payload(external_id, "01099880002")
    await _post(client, raw)
    again = await _post(client, raw)

    assert again["status"] == "duplicate"
    assert again["outcome"] == "duplicate"
    row = await _message_row(session, external_id)
    assert row.detail is not None and "outcome=duplicate" in row.detail


async def test_an_unmatched_sender_is_its_own_outcome(client: Any, session: Any) -> None:
    external_id = f"wamid.r17-unknown-{uuid.uuid4().hex[:8]}"
    result = await _post(client, _payload(external_id, "01099999999"))

    assert result["outcome"] == "created"
    row = await _message_row(session, external_id)
    assert row.detail is not None and "matched=unknown" in row.detail
    # `matched=unknown` is the row that explains "a request appeared with no client" — it is not a failure
    assert row.status == "ok"


async def test_a_lead_match_is_named_as_lead_activity_not_silence(client: Any, session: Any) -> None:
    """The dangerous case: 200, no request, and until now nothing on screen said why.

    A Lead has no request to append to by design, so the caller gets `request_id: null`. That is correct — and
    it is exactly what an operator would read as "the webhook is broken". `outcome=lead_activity` says it.
    """
    external_id = f"wamid.r17-lead-{uuid.uuid4().hex[:8]}"
    # `lead_a` from the seed (`mobile=+201000000001`); the message must carry the same normalized number
    result = await _post(client, _payload(external_id, "201000000001"))

    assert result["matched_kind"] == "lead"
    assert result["outcome"] == "lead_activity"
    assert result["request_id"] is None
    row = await _message_row(session, external_id)
    assert row.detail is not None
    assert "outcome=lead_activity" in row.detail
    assert "matched=lead" in row.detail


async def test_the_detail_is_short_enough_for_the_column(client: Any, session: Any) -> None:
    """`webhook_events.detail` is clamped, and a truncated outcome would be worse than none."""
    external_id = f"wamid.r17-len-{uuid.uuid4().hex[:8]}"
    await _post(client, _payload(external_id, "01099880003"))
    row = await _message_row(session, external_id)
    assert row.detail is not None and len(row.detail) <= 900
