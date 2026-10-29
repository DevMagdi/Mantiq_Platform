"""Finance routes (R10): ledger, customer AR, commission payouts, cashbox.

Gating is per-verb and deliberate (ADR-030 §5):

* reading anything here needs `finance.view` — a rep does not hold it, which is what keeps the office cash
  position off a rep's screen (a hidden button is not isolation; a `403` is);
* posting needs `finance.post`, voiding needs `finance.void` (taking history back is the rarer power);
* approving and paying a commission needs `commissions.manage`.

Not flag-gated. Phase 2's `phase2.enabled` guards *issuance* because a half-configured workspace must not
book policies; the ledger is the office's book of record and must not be switchable off silently — the
permission is the gate.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.v1.deps import (
    CommissionsManage,
    DbSession,
    FinancePeriodClose,
    FinancePost,
    FinanceView,
    FinanceVoid,
    PageQuery,
    ScopeDep,
)
from app.core.errors import NotFound, ValidationFailed
from app.domain.enums import LedgerDirection, LedgerEntryType, PaymentMethod
from app.models.clients import Client
from app.models.phase2 import Policy
from app.repositories.scope_filters import row_visible
from app.schemas.out import (
    ArClientDetailOut,
    ArClientPageOut,
    ArSummaryOut,
    CashboxOut,
    LedgerEntryOut,
    LedgerPageOut,
    LedgerVoidOut,
    SettlementOut,
    SettlementPageOut,
    SpendByPeriodOut,
)
from app.services import finance as svc

router = APIRouter(prefix="/finance", tags=["finance"])

EntryTypeLiteral = Literal["premium_receivable", "commission_receivable", "commission_payable", "expense", "adjustment"]
DirectionLiteral = Literal["in", "out"]
MethodLiteral = Literal["cash", "bank", "wallet", "other"]


class LedgerEntryCreateIn(BaseModel):
    """A manual movement. `amount` is rejected at the schema for `<= 0`, and again in the service."""

    entry_type: EntryTypeLiteral
    direction: DirectionLiteral
    amount: float = Field(gt=0, description="Always positive — `direction` carries the sign")
    currency: str = Field(default="EGP", min_length=3, max_length=8)
    client_id: UUID | None = None
    policy_id: UUID | None = None
    installment_id: UUID | None = None
    user_id: UUID | None = None
    method: MethodLiteral | None = None
    note: str | None = Field(default=None, max_length=500)


class LedgerVoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500, description="Why the row is being taken back — kept forever")


class CommissionPayIn(BaseModel):
    method: MethodLiteral = "bank"
    note: str | None = Field(default=None, max_length=500)


class CommissionApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


# ------------------------------------------------------------------ ledger ---------------------------
@router.get("/ledger", summary="The ledger (append-only money movements)", response_model=LedgerPageOut)
async def list_ledger(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: FinanceView,
    q: str | None = Query(default=None, max_length=120),
    status: Literal["posted", "void"] | None = None,
    entry_type: EntryTypeLiteral | None = None,
    direction: DirectionLiteral | None = None,
    client_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Any:
    return await svc.list_entries(
        session,
        scope,
        params,
        search=q,
        status=status,
        entry_type=entry_type,
        direction=direction,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/ledger", status_code=201, summary="Post a ledger entry", response_model=LedgerEntryOut)
async def create_ledger_entry(
    session: DbSession,
    scope: ScopeDep,
    body: LedgerEntryCreateIn,
    actor: FinancePost,
    request: Request,
) -> Any:
    """Manual posting (an expense, an adjustment, a receivable). Out-of-scope clients are a `404`."""
    if (
        body.client_id is not None
        and (await row_visible(session, select(Client).where(Client.id == body.client_id), scope, "client")) is None
    ):
        raise NotFound("Client not found")
    policy_id = body.policy_id
    client_id = body.client_id
    if policy_id is not None:
        policy = await row_visible(session, select(Policy).where(Policy.id == policy_id), scope, "policy")
        if policy is None:
            raise NotFound("Policy not found")
        client_id = client_id or policy.client_id
    if body.entry_type == "premium_receivable" and policy_id is None:
        raise ValidationFailed("A premium receipt must name the policy it is against", details={"field": "policy_id"})
    entry = await svc.post_entry(
        session,
        actor,
        entry_type=LedgerEntryType(body.entry_type),
        direction=LedgerDirection(body.direction),
        amount=body.amount,
        currency=body.currency,
        client_id=client_id,
        policy_id=policy_id,
        installment_id=body.installment_id,
        user_id=body.user_id,
        method=PaymentMethod(body.method) if body.method else None,
        note=body.note,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response = svc.entry_brief(entry)
    await session.commit()
    return response


@router.post("/ledger/{entry_id}/void", summary="Void a posted entry (reverses every total)", response_model=LedgerVoidOut)
async def void_ledger_entry(
    entry_id: UUID,
    session: DbSession,
    scope: ScopeDep,
    body: LedgerVoidIn,
    actor: FinanceVoid,
    request: Request,
) -> Any:
    entry, reopened = await svc.void_entry(
        session,
        actor,
        entry_id,
        body.reason,
        scope=scope,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    balance = await svc.cashbox(session, scope)
    # `reopened` is the business fact the void put back (an installment, a commission payout) — without it
    # the operator sees money move and has to go and check whether the plan followed (R10.1)
    response = {"entry": svc.entry_brief(entry), "cashbox_balance": balance["balance"], "reopened": reopened}
    await session.commit()
    return response


@router.get("/spend-by-period", summary="ERP hub: posted in/out totals for a period", response_model=SpendByPeriodOut)
async def spend_by_period(
    session: DbSession,
    scope: ScopeDep,
    actor: FinanceView,
    period: Literal["month", "quarter", "semiannual", "year"] = Query(default="month"),
) -> Any:
    return await svc.spend_by_period(session, scope, period)


class PeriodCloseIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")
    note: str | None = Field(default=None, max_length=500)


class OpeningBalanceIn(BaseModel):
    amount: float = Field(ge=0)
    effective_at: Any = Field(description="ISO datetime the counted drawer applies from")
    note: str | None = Field(default=None, max_length=500)


@router.get("/period-closes", summary="Closed finance months (ADR-033)")
async def period_closes(session: DbSession, scope: ScopeDep, actor: FinanceView) -> Any:
    return {"items": await svc.list_period_closes(session)}


@router.post("/period-closes", status_code=201, summary="Close a finance month (finance.period_close)")
async def close_period(body: PeriodCloseIn, request: Request, session: DbSession, scope: ScopeDep, actor: FinancePeriodClose) -> Any:
    result = await svc.close_period(session, actor, body.period, note=body.note, ip=request.client.host if request.client else None)
    await session.commit()
    return result


@router.delete("/period-closes/{period}", summary="Reopen a closed finance month (finance.period_close)")
async def reopen_period(period: str, request: Request, session: DbSession, scope: ScopeDep, actor: FinancePeriodClose) -> Any:
    result = await svc.reopen_period(session, actor, period, ip=request.client.host if request.client else None)
    await session.commit()
    return result


@router.post("/cashbox/opening", status_code=201, summary="Set the cashbox opening balance (finance.post)")
async def set_opening(body: OpeningBalanceIn, request: Request, session: DbSession, scope: ScopeDep, actor: FinancePost) -> Any:
    from datetime import datetime as _dt

    effective = body.effective_at if isinstance(body.effective_at, _dt) else _dt.fromisoformat(str(body.effective_at))
    result = await svc.set_opening_balance(
        session, actor, amount=body.amount, effective_at=effective, note=body.note, ip=request.client.host if request.client else None
    )
    await session.commit()
    return result


@router.get("/spend-by-period/export.xlsx", summary="ERP hub: the period report as Excel (server-computed)")
async def spend_export(
    session: DbSession,
    scope: ScopeDep,
    actor: FinanceView,
    period: Literal["month", "quarter", "semiannual", "year"] = Query(default="month"),
) -> Any:
    from fastapi.responses import Response

    from app.services.exports import period_workbook

    data = await svc.spend_by_period(session, scope, period)
    content = period_workbook(data)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="erp-period-{data["label"]}.xlsx"'},
    )


# ------------------------------------------------------------------ AR ------------------------------
@router.get("/ar/summary", summary="Customer AR: totals and aging buckets", response_model=ArSummaryOut)
async def ar_summary(session: DbSession, scope: ScopeDep, actor: FinanceView) -> Any:
    return await svc.ar_summary(session, scope)


@router.get("/ar/clients", summary="Customer AR per client (aging 0-30 / 31-60 / 61-90 / 90+)", response_model=ArClientPageOut)
async def ar_clients(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: FinanceView,
    q: str | None = Query(default=None, max_length=120),
    bucket: Literal["current", "d0_30", "d31_60", "d61_90", "d90_plus"] | None = None,
) -> Any:
    return await svc.ar_clients(session, scope, params, search=q, bucket=bucket)


@router.get("/ar/clients/{client_id}", summary="One client's open installments and receipts", response_model=ArClientDetailOut)
async def ar_client(client_id: UUID, session: DbSession, scope: ScopeDep, actor: FinanceView) -> Any:
    return await svc.ar_client(session, scope, client_id)


# ------------------------------------------------------------------ commissions ---------------------
@router.get("/commissions", summary="Commissions and their payout state", response_model=SettlementPageOut)
async def list_commissions(
    session: DbSession,
    scope: ScopeDep,
    params: PageQuery,
    actor: FinanceView,
    status: Literal["accrued", "approved", "paid", "cancelled"] | None = None,
    q: str | None = Query(default=None, max_length=120),
) -> Any:
    return await svc.list_commissions(session, scope, params, status=status, search=q)


@router.post("/commissions/{commission_id}/approve", summary="Approve an accrued commission", response_model=SettlementOut)
async def approve_commission(
    commission_id: UUID,
    session: DbSession,
    scope: ScopeDep,
    actor: CommissionsManage,
    request: Request,
    body: CommissionApproveIn | None = None,
) -> Any:
    result = await svc.approve_commission(
        session,
        actor,
        scope,
        commission_id,
        note=body.note if body else None,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return result


@router.post("/commissions/{commission_id}/pay", summary="Pay a commission (writes the ledger once)", response_model=SettlementOut)
async def pay_commission(
    commission_id: UUID,
    session: DbSession,
    scope: ScopeDep,
    actor: CommissionsManage,
    request: Request,
    body: CommissionPayIn | None = None,
) -> Any:
    result = await svc.pay_commission(
        session,
        actor,
        scope,
        commission_id,
        method=PaymentMethod(body.method) if body else PaymentMethod.BANK,
        note=body.note if body else None,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return result


# ------------------------------------------------------------------ cashbox -------------------------
@router.get("/cashbox", summary="The office cashbox (posted cash rows only)", response_model=CashboxOut)
async def cashbox(session: DbSession, scope: ScopeDep, actor: FinanceView) -> Any:
    return await svc.cashbox(session, scope)
