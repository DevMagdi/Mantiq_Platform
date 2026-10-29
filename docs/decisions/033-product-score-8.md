# ADR-033 — Product Score-8 sprint: operational ERP + pilot readiness (2026-10-29)

## Status
Accepted — Product Scorecard Sprint (axes A–E). This is an **operational** ERP for a brokerage office; it is explicitly **not** official tax books and claims no FRA certification (sprint rule 8).

## Decisions
1. **Cashbox opening balance is its own table** (`cashbox_openings`: amount, effective_at, note, created_by), not a ledger entry type. The ledger stays a pure movement log (ADR-030); the cashbox reads `balance = opening + net posted cash movements since the opening`, and keeps the «صافي حركات» disclaimer verbatim until an opening exists (E1/E4).
2. **Period close is a hard block with a named override** (`finance_period_closes`: `YYYY-MM` unique, closed_by/at, note). Posting into a closed month is refused for everyone except holders of the new `finance.period_close` permission, whose post is allowed but written to audit as `ledger.posted_in_closed_period` (E3). Reopening requires the same permission and is audited. Voiding into a closed period follows the same rule.
3. **New permission `finance.period_close`** (system_admin + sales_manager). Existing finance permissions are unchanged; reps still have no finance access at all.
4. **Period export is server-side Excel** (openpyxl, already a dependency) from the same aggregate as `spend-by-period` — the screen never computes money (E2/E5).
5. **Performance by index + aggregation, not by lowering gates**: composite indexes on the ledger/request hot sorts, and the cashbox collapsed from ~10 sum queries into grouped aggregates (C1). Before/after numbers live in `docs/perf/product-score8-2026-10-29.md`; a failed gate on weak hardware is recorded, never silently relaxed.
6. **Pilot surface**: `/setup` (owner first-run wizard over existing settings/users APIs) and `/ops` (health summary over healthz/readyz + integration states + storage usage) are read-mostly screens over existing APIs; privacy + call-recording consent texts ship in-product and in the runbook as editable Arabic defaults, explicitly «not a substitute for a lawyer» (B1/B3/B4).
7. **«اليوم» (Today) board** aggregates data the system already owns — overdue SLA requests, due lead follow-ups, installments due within 14 days — behind existing scopes; no new tables (A5).
