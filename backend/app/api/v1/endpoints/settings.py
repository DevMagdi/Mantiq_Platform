"""Settings hub routes (spec §8, ADR-016).

One screen, ten routes, one permission (`settings.manage`). Two rules are visible in the shapes below and
are the reason this module exists at all:

* a provider read returns **field status, never field values** for the secret half — the response model has
  no slot for a plaintext secret, so forgetting to mask is not possible here;
* the portal client never reaches this router at all (`settings.manage` is not on any portal role), and
  the users/permissions tab mounted in the same screen keeps its own `users.manage` gate — grouping the UI
  did not widen anybody's reach.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import DbSession, SettingsManage
from app.core.errors import NotFound, ValidationFailed
from app.models.integrations import PROVIDERS
from app.schemas.common import EmailAddress
from app.services import integrations_settings as integrations
from app.services import policy_settings

router = APIRouter(tags=["settings"])


class SecretFieldOut(BaseModel):
    key: str
    set: bool = Field(description="whether a value is stored — the only fact a GET may reveal")
    display: str = Field(description="`••••••••` when set, `not_set` otherwise; never a prefix of the value")


class ProviderStateOut(BaseModel):
    provider: str
    config: dict[str, Any]
    secrets: list[SecretFieldOut]
    is_live: bool
    status: str = Field(description="live | ready | unset | error — legacy badge tone (R9 §1.D)")
    connection_status: str = Field(default="not_connected", description="connected | not_connected | error (R12 §A)")
    missing_fields: list[str] = Field(default_factory=list, description="named credentials still missing")
    last_webhook_at: str | None = None
    last_error: str | None = None
    last_tested_at: str | None = None
    last_test_status: str | None = None
    last_test_detail: str | None = None
    updated_at: str | None = None


class IntegrationWriteIn(BaseModel):
    """Partial update. `secrets[key] = null` removes it; `""` (or omitting the key) leaves it untouched."""

    config: dict[str, Any] | None = None
    secrets: dict[str, Any] | None = None
    is_live: bool | None = None
    confirm_live: bool = Field(default=False, description="required to flip a provider to live, after a green test")


class LiveSwitchIn(BaseModel):
    is_live: bool
    #: The explicit confirmation. `confirm_live` is accepted as an alias because `PUT /settings/integrations`
    #: calls the same thing `confirm_live` — a client that copied that field name used to get a 409 that
    #: said "needs confirmation" while it was busy confirming.
    confirm: bool = False
    confirm_live: bool = False


class PreflightBlockerOut(BaseModel):
    code: str = Field(description="confirm | test | fields")
    message: str
    fields: list[str] = []


class PreflightOut(BaseModel):
    provider: str
    ready: bool = Field(description="true when nothing stands between this provider and live mode")
    blockers: list[PreflightBlockerOut]
    missing: list[str] = Field(description="machine keys of the fields the live switch requires")
    labels: list[str] = Field(description="Arabic labels for `missing`, for the message the operator reads")
    is_live: bool
    mode: str = Field(description="live | ready — `sim` no longer exists in production (R9)")
    sim_allowed: bool = Field(description="false in production: the screen hides the switch back to simulation")
    status: str
    test_status: str | None = None
    tested_at: str | None = None
    next: str


class ConnectionTestIn(BaseModel):
    to: EmailAddress | None = Field(default=None, description="send a real test email to this address (email provider, live mode only)")


class ConnectionTestOut(BaseModel):
    provider: str
    ok: bool
    detail: str
    took_ms: int
    mode: str
    tested_at: str


class TemplateSyncOut(BaseModel):
    provider: str
    templates: list[dict[str, Any]]
    detail: str
    synced_at: str


class WebhookUrlOut(BaseModel):
    provider: str
    label: str
    url: str
    note: str


class WebhookUrlsOut(BaseModel):
    base_url: str
    items: list[WebhookUrlOut]


class WebhookEventOut(BaseModel):
    id: str
    provider: str
    event_type: str
    external_id: str | None = None
    status: str
    detail: str | None = None
    occurred_at: str | None = None


class UnderwritingDefaultsIn(BaseModel):
    default_commission_pct: float | None = Field(default=None, ge=0, le=100)
    default_payment_plan: str | None = None
    default_currency: str | None = Field(default=None, min_length=3, max_length=8)


class UnderwritingDefaultsOut(BaseModel):
    default_commission_pct: float
    default_payment_plan: str
    default_currency: str


@router.get(
    "/settings/integrations", summary="Integration providers: config, secret status, live/sim", response_model=list[ProviderStateOut]
)
async def list_integrations(session: DbSession, _actor: SettingsManage) -> list[dict[str, Any]]:
    return await integrations.all_states(session)


@router.get(
    "/settings/integrations/webhook-urls",
    summary="Callback URLs to paste into Meta / the mobile-calls provider",
    response_model=WebhookUrlsOut,
)
async def webhook_urls(_actor: SettingsManage) -> dict[str, Any]:
    return integrations.webhook_urls()


@router.get("/settings/integrations/events", summary="Recent inbound webhook events (last 24h)", response_model=list[WebhookEventOut])
async def integration_events(
    session: DbSession,
    _actor: SettingsManage,
    provider: Annotated[str | None, Query(description="whatsapp | telephony | email")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict[str, Any]]:
    if provider is not None and provider not in PROVIDERS:
        raise NotFound("Unknown provider")
    return await integrations.recent_events(session, provider, limit=limit)


@router.get("/settings/policies", summary="Underwriting defaults (Phase 2)", response_model=UnderwritingDefaultsOut)
async def get_underwriting(session: DbSession, _actor: SettingsManage) -> dict[str, Any]:
    return await policy_settings.get_defaults(session)


@router.put("/settings/policies", summary="Save underwriting defaults", response_model=UnderwritingDefaultsOut)
async def put_underwriting(
    request: Request,
    session: DbSession,
    actor: SettingsManage,
    body: UnderwritingDefaultsIn = Body(...),
) -> dict[str, Any]:
    values = {key: value for key, value in body.model_dump().items() if value is not None}
    return await policy_settings.set_defaults(
        session,
        actor,
        values,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/settings/integrations/whatsapp/sync-templates", summary="Pull approved WhatsApp templates from Meta", response_model=TemplateSyncOut
)
async def sync_templates(request: Request, session: DbSession, actor: SettingsManage) -> dict[str, Any]:
    return await integrations.sync_templates(
        session,
        actor,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.get("/settings/integrations/{provider}", summary="One provider: config, secret status, live/sim", response_model=ProviderStateOut)
async def get_integration(provider: str, session: DbSession, _actor: SettingsManage) -> dict[str, Any]:
    _check_provider(provider)
    return (await integrations.state(session, provider)).as_dict()


@router.put(
    "/settings/integrations/{provider}", summary="Save a provider (partial; secrets are write-only)", response_model=ProviderStateOut
)
async def put_integration(
    provider: str,
    request: Request,
    session: DbSession,
    actor: SettingsManage,
    body: IntegrationWriteIn = Body(...),
) -> dict[str, Any]:
    _check_provider(provider)
    if body.config is None and body.secrets is None and body.is_live is None:
        raise ValidationFailed("لا تغيير مطلوب", details=[{"field": "body", "message": "config أو secrets أو is_live", "type": "missing"}])
    return await integrations.update(
        session,
        actor,
        provider,
        config=body.config,
        secrets=body.secrets,
        is_live=body.is_live,
        confirm_live=body.confirm_live,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.get(
    "/settings/integrations/{provider}/preflight",
    summary="Can this provider go live? The blockers, before the button is offered",
    response_model=PreflightOut,
)
async def integration_preflight(provider: str, session: DbSession, actor: SettingsManage) -> dict[str, Any]:
    _check_provider(provider)
    return await integrations.preflight(session, provider)


@router.post("/settings/integrations/{provider}/test", summary="Test the provider connection (6/min)", response_model=ConnectionTestOut)
async def run_integration_test(
    provider: str,
    request: Request,
    session: DbSession,
    actor: SettingsManage,
    body: ConnectionTestIn | None = None,
) -> dict[str, Any]:
    _check_provider(provider)
    return await integrations.run_connection_test(
        session,
        actor,
        provider,
        to=body.to if body else None,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post(
    "/settings/integrations/{provider}/live",
    summary="Flip simulation ↔ live (live needs a green test + confirm)",
    response_model=ProviderStateOut,
)
async def switch_live(
    provider: str,
    request: Request,
    session: DbSession,
    actor: SettingsManage,
    body: LiveSwitchIn = Body(...),
) -> dict[str, Any]:
    _check_provider(provider)
    return await integrations.set_live(
        session,
        actor,
        provider,
        is_live=body.is_live,
        confirm=body.confirm or body.confirm_live,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise NotFound("Unknown provider")
