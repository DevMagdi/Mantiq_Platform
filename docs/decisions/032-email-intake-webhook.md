# ADR-032 — Email intake is a signed provider webhook; settings show connection, not "live mode" (R12)

## Status
Accepted — 2026-10-28 (R12). Narrows ADR-027's operator surface; sim drivers stay dev/test-only per ADR-029.

## Context
R12 §D allowed "webhook provider OR IMAP poll worker". The repo has no worker/queue runtime, and adding an IMAP poller would mean a second always-on process with credential storage and lease logic — while every other channel already enters through `/api/v1/webhooks/*` with the same idempotent intake service. §A also removes the sim↔live switch from the staff UI, which ADR-027's preflight/`is_live` operator flow embodied.

## Decision
1. **Email intake = signed webhook.** `POST /api/v1/webhooks/email` accepts `{external_id, from, from_name, subject, body, message_id?, attachments[{filename, mime, content_base64}]}` from a transactional-inbound provider (SendGrid Inbound Parse adapter, Mailgun route, SES→SNS adapter, or any bridge) and requires `X-Email-Signature: sha256=HMAC(inbound_secret, raw_body)` when the `inbound_secret` credential is saved. Attachments go through the one MIME allowlist/size cap; a refused type is a 422, never a silent drop.
2. **Threading:** a new `external_id` opens/updates the email-channel Request keyed first by provider thread/message id, then by sender email's open request; a replayed `external_id` returns `status: duplicate` and writes nothing; a new message from a known sender appends (`status: appended`). Outbound staff email replies reuse the existing channel reply path; provider failures land on the message row as FAILED (same contract as WhatsApp).
3. **Connection, not mode.** Settings → Integrations derives `connection_status` (`connected | not_connected | error`) from saved credentials + last connection test, names `missing_fields`, and shows `last_webhook_at`/`last_error`. There is no go-live switch, no sim toggle, and no silent sim fallback in operator flows; sim drivers and `/api/v1/dev/sim*` exist only when `ENV ∈ {dev, test}` (unchanged, ADR-029). The preflight/live endpoints remain API-compatible for non-UI callers.
4. **Media is one model.** Inbound WhatsApp/email files and staff uploads share the existing `attachments` table (owner `request`, linked to their message via `owner_id`), signed short-lived staff URLs, and audited `attachment.accessed` events. No new migration was needed.

## Consequences
Operators connect a channel by pasting real credentials and the webhook URL from the same card; "it works" is proven by `last_webhook_at` moving. An IMAP-only mailbox needs a provider bridge in front — documented in `docs/runbook.md`. Load evidence and its hardware fails: `docs/perf/r12-load-2026-10-28.md`.
