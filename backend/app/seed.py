"""Deterministic demo seed (fake data only — spec rule 4). Idempotent: safe to re-run."""

from __future__ import annotations

import csv
import os
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.domain import permissions as P
from app.domain.enums import (
    ActivityType,
    Channel,
    ClientStatus,
    ClientType,
    CommissionStatus,
    CoverageStatus,
    ImportJobStatus,
    ImportJobType,
    InstallmentStatus,
    InsuranceTypeCode,
    IssuingDocumentStatus,
    IssuingStage,
    LeadStatus,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    MessageDirection,
    PaymentMethod,
    PaymentPlan,
    PolicyStatus,
    PortalRole,
    RequestPriority,
    RequestStatus,
    ScopeType,
    SettlementStatus,
)
from app.domain.phone import normalize_mobile
from app.models.auth import ClientPortalUser, Permission, Role, User, UserScope
from app.models.clients import Client, ClientContact, ImportJob, InsuredPerson
from app.models.finance import CommissionSettlement, LedgerEntry
from app.models.leads import Lead, LeadActivity, LeadMeeting
from app.models.masters import (
    BusinessHoursRow,
    DocumentRequirement,
    FeatureFlag,
    InsuranceType,
    Insurer,
    LostReason,
    SlaPolicy,
)
from app.models.phase2 import Commission, Installment, IssuingCase, IssuingDocument, Policy
from app.models.requests import (
    InternalNote,
    RequestMessage,
    RequestNumberCounter,
    ServiceRequest,
)

RNG = random.Random(20261026)
REPS = [
    ("sara.elsayed@brokerhub.test", "سara السيد"),
    ("mostafa.adel@brokerhub.test", "مصطفى عادل"),
    ("nour.fahmy@brokerhub.test", "نور فهمي"),
    ("karim.hassan@brokerhub.test", "كريم حسن"),
]
# Demo-only credential for the seeded accounts. Override it in every real environment with
# `SEED_PASSWORD`; the value below exists so `python -m app.seed` stays copy-pasteable in a dev box.
PASSWORD = os.environ.get("SEED_PASSWORD", "Br0kerHub!2026")

INSURERS_CSV = Path(__file__).with_name("data") / "insurers_eg.csv"


def _insurers() -> list[tuple[str, str, str]]:
    """The register of licensed Egyptian insurers (spec §5).

    Names come from the FRA statistical yearbook and live in a CSV so the same file can be replayed
    through `POST /api/v1/insurers/import-csv` in a real deployment — the seed never invents companies.
    """
    with INSURERS_CSV.open(encoding="utf-8-sig", newline="") as handle:
        return [(row["code"], row["name"], row.get("name_en") or "") for row in csv.DictReader(handle)]


INSURANCE_TYPES = [
    (InsuranceTypeCode.MEDICAL.value, "طبي", "Medical"),
    (InsuranceTypeCode.GENERAL.value, "عام", "General"),
    (InsuranceTypeCode.MEDICAL_GENERAL.value, "طبي وعام", "Medical & General"),
    (InsuranceTypeCode.LIFE.value, "حياة", "Life"),
    (InsuranceTypeCode.MOTOR.value, "توك تيل", "Motor"),
    (InsuranceTypeCode.TRAVEL.value, "سفر", "Travel"),
]

DOC_REQUIREMENTS = {
    InsuranceTypeCode.MEDICAL.value: ["بطاقة ضريبية", "سجل تجاري", "كشف بأعداد الموظفين", "خطاب تغطية سابق"],
    InsuranceTypeCode.GENERAL.value: ["بطاقة ضريبية", "سجل تجاري", "قائمة الأصول"],
    InsuranceTypeCode.MEDICAL_GENERAL.value: ["بطاقة ضريبية", "سجل تجاري", "كشف بأعداد الموظفين", "الميزانية"],
}

LOST_REASONS = ["سعر أعلى", "تعامل مع وسيط آخر", "تأجيل الخطة", "لم يرد", "ميزانية غير كافية"]

COMPANIES = [
    "منطق للبرمجيات",
    "النيل للتجارة",
    "شركة الأمل",
    "دار الهندسة",
    "مصر للتوزيع",
    "واحة الطبية",
]


async def seed(session: AsyncSession, *, reset: bool = False) -> dict[str, int]:
    if await session.execute(select(func.count(User.id))) and not reset:
        if (await session.execute(select(func.count(Role.id)))).scalar_one():
            return {"skipped": 1}

    # --- roles & permissions ---------------------------------------------------------
    perms = {code: Permission(code=code, group=code.split(".")[0], description=None) for code in P.ALL_PERMISSIONS}
    session.add_all(perms.values())
    roles: dict[str, Role] = {}
    for code, name in P.DEFAULT_ROLES.items():
        role = Role(code=code, name=name, is_system=True)
        role.permissions = [perms[c] for c in P.ROLE_PERMISSIONS[code] if c in perms]
        session.add(role)
        roles[code] = role
    await session.flush()

    # --- users ----------------------------------------------------------------------
    users: dict[str, User] = {}
    for key, code in (("manager", "sales_manager"), ("cs", "customer_service"), ("am", "account_manager")):
        user = User(
            email=f"{key}@brokerhub.test",
            password_hash=hash_password(PASSWORD),
            full_name={"manager": "مدير المبيعات", "cs": "منى خدمة العملاء", "am": "حسام مدير حسابات"}[key],
            role_id=roles[code].id,
        )
        session.add(user)
        users[key] = user
    session.add(
        User(
            email="admin@brokerhub.test",
            password_hash=hash_password(PASSWORD),
            full_name="مدير النظام",
            role_id=roles["system_admin"].id,
        )
    )
    for email, name in REPS:
        user = User(email=email, password_hash=hash_password(PASSWORD), full_name=name, role_id=roles["sales_rep"].id)
        session.add(user)
        users[email] = user
    await session.flush()

    # --- masters --------------------------------------------------------------------
    insurers = {}
    for code, name, name_en in _insurers():
        row = Insurer(code=code, name=name, name_en=name_en or code.upper())
        session.add(row)
        insurers[code] = row
    types: dict[str, InsuranceType] = {}
    for code, type_name, name_en in INSURANCE_TYPES:
        type_row = InsuranceType(code=code, name=type_name, name_en=name_en)
        session.add(type_row)
        types[code] = type_row
        for idx, doc_name in enumerate(DOC_REQUIREMENTS.get(code, [])):
            session.add(DocumentRequirement(insurance_type=type_row, name=doc_name, is_required=True, sort_order=idx))
    lost_reasons: list[LostReason] = []
    for idx, reason in enumerate(LOST_REASONS):
        reason_row = LostReason(name=reason, sort_order=idx)
        session.add(reason_row)
        lost_reasons.append(reason_row)
    for priority, (first, resolution) in {
        RequestPriority.URGENT: (30, 240),
        RequestPriority.HIGH: (60, 480),
        RequestPriority.NORMAL: (240, 1440),
        RequestPriority.LOW: (720, 4320),
    }.items():
        session.add(SlaPolicy(priority=priority, first_response_minutes=first, resolution_minutes=resolution, warn_before_minutes=45))
    for weekday in range(7):
        is_open = weekday not in {4, 5}
        session.add(
            BusinessHoursRow(
                weekday=weekday,
                open_time=datetime.strptime("09:00", "%H:%M").time(),
                close_time=datetime.strptime("18:00", "%H:%M").time(),
                is_open=is_open,
            )
        )
    for key, enabled, description in (
        ("phase2.enabled", False, "الوثائق والأقساط والتقارير والذكاء الاصطناعي (Phase 2)"),
        ("ai.enabled", False, "AI provider (transcription, summaries, suggested replies)"),
        ("whatsapp.enabled", True, "WhatsApp Cloud API integration"),
        ("telephony.enabled", True, "Call centre integration"),
    ):
        session.add(FeatureFlag(key=key, enabled=enabled, description=description))
    await session.flush()

    # --- clients (3 real ones + 3 from conversions) ----------------------------------
    clients: list[Client] = []
    for idx, name in enumerate(COMPANIES[:3]):
        client = Client(
            type=ClientType.COMPANY,
            name=name,
            status=ClientStatus.ACTIVE,
            account_owner_id=users[REPS[idx % len(REPS)][0]].id,
            since_date=date(2024 + (idx % 2), 1 + idx, 5 + idx),
        )
        session.add(client)
        clients.append(client)
    await session.flush()

    for client in clients:
        session.add(
            ClientContact(
                client=client,
                name=f"مسؤول {client.name}",
                title="HR Manager",
                mobile=normalize_mobile(f"0100123{RNG.randint(1000, 9999)}"),
                email=f"hr{RNG.randint(1, 99)}@company.test",
                is_primary=True,
            )
        )
        for i in range(RNG.randint(6, 14)):
            start = date(2025, 1, 1) + timedelta(days=RNG.randint(0, 200))
            session.add(
                InsuredPerson(
                    client_id=client.id,
                    name=f"موظف {i + 1} · {client.name[:8]}",
                    mobile=normalize_mobile(f"011{RNG.randint(100000000, 999999999)}"),
                    department=RNG.choice(["المالية", "الموارد البشرية", "تقنية المعلومات", "المبيعات", "العمليات"]),
                    job_title=RNG.choice(["محاسب", "مطوّر", "أخصائي", "مشرف"]),
                    coverage_status=RNG.choice(
                        [CoverageStatus.ACTIVE, CoverageStatus.ACTIVE, CoverageStatus.PENDING, CoverageStatus.ENDED]
                    ),
                    coverage_start=start,
                    coverage_end=start + timedelta(days=365),
                    external_ref=f"EMP-{RNG.randint(1000, 9999)}",
                )
            )
        session.add(
            Policy(
                client_id=client.id,
                insurer_id=insurers[RNG.choice(list(insurers))].id,
                insurance_type_id=types[InsuranceTypeCode.MEDICAL.value].id,
                policy_no=f"GRP-{2026}-{RNG.randint(10000, 99999)}",
                start_date=date(2025, 6, 1),
                end_date=date(2026, 5, 31) if RNG.random() < 0.5 else date(2027, 3, 31),
                premium_amount=Decimal(RNG.randint(180_000, 900_000)),
                payment_plan=RNG.choice(list(PaymentPlan)),
                commission_rate=Decimal("5.00"),
                status=PolicyStatus.ISSUED,
                # R10.1: an issued policy carries its issue date. The demo rows have no `policy.issued` audit
                # trail (they were never issued through the screen), and migration 0010 deliberately leaves
                # such rows with `issued_at = NULL` rather than a guessed `created_at` — so the seed states
                # the date it means, inside the current month, where the executive KPI can read it.
                # deterministic on purpose: `RNG` is one shared stream, and drawing from it here shifted every
                # later choice (insured names, call subjects) and silently changed the demo graph the smoke
                # script and the e2e fixtures rely on.
                issued_at=max(
                    datetime.now(UTC) - timedelta(days=3),
                    datetime.now(UTC).replace(day=1, hour=9, minute=0, second=0, microsecond=0),
                ),
                headcount=RNG.randint(12, 80),
            )
        )
    await session.flush()

    for policy in (await session.execute(select(Policy))).scalars():
        premium = float(policy.premium_amount or 0)
        plan_months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "cash": 12}[policy.payment_plan.value]
        count = 12 // plan_months or 1
        each = Decimal(round(premium / count, 2)) if count else Decimal(premium)
        for seq in range(count):
            due = (policy.start_date or date(2025, 6, 1)) + timedelta(days=30 * (seq + 1) * (plan_months or 1))
            paid = seq < count // 2
            installment = Installment(
                policy_id=policy.id,
                sequence_no=seq + 1,
                due_date=due,
                amount=each,
                paid_at=datetime.now(UTC) - timedelta(days=5) if paid else None,
                status=InstallmentStatus.PAID if paid else (InstallmentStatus.LATE if due < date.today() else InstallmentStatus.UPCOMING),
            )
            session.add(installment)
            if paid:
                session.add(
                    # accrued, not settled: a commission on a paid installment is *owed* until finance
                    # pays it, and R10's settlement rows below are what records that (ADR-030 §4)
                    Commission(
                        policy_id=policy.id,
                        amount=round(each * Decimal("0.05"), 2),
                        status=CommissionStatus.ACCRUED,
                    )
                )
    await session.flush()

    # --- R10: the ledger the finance screens read (ADR-030) --------------------------
    # Seeded money is not invented: every posted entry below is the receipt of an installment the seed
    # already marked paid, or the payout of a commission the policy accrual already wrote. The pattern
    # (paid / approved / accrued) gives the commissions screen all three states to render.
    cashier = users["manager"]
    ledger_seq = 0
    receipts = (
        await session.execute(
            select(Installment, Policy.client_id, Policy.policy_no)
            .join(Policy, Installment.policy_id == Policy.id)
            .where(Installment.paid_at.is_not(None))
        )
    ).all()
    for installment, client_id, _policy_no in receipts:
        ledger_seq += 1
        # most of the office's money arrives as cash; a third of it as a transfer, so `by_method` is real
        method = PaymentMethod.CASH if ledger_seq % 3 else PaymentMethod.BANK
        session.add(
            LedgerEntry(
                entry_no=f"LE-{ledger_seq:06d}",
                entry_type=LedgerEntryType.PREMIUM_RECEIVABLE,
                direction=LedgerDirection.IN,
                amount=installment.amount,
                currency="EGP",
                status=LedgerStatus.POSTED,
                client_id=client_id,
                policy_id=installment.policy_id,
                installment_id=installment.id,
                method=method,
                note=f"تحصيل قسط {installment.sequence_no}",
                posted_at=installment.paid_at or datetime.now(UTC),
                posted_by=cashier.id,
            )
        )

    # two real office expenses: the cashbox must not equal "everything ever collected"
    for idx, (label, amount) in enumerate((("إيجار المكتب", 18000), ("مصاريف تشغيل", 4250))):
        ledger_seq += 1
        session.add(
            LedgerEntry(
                entry_no=f"LE-{ledger_seq:06d}",
                entry_type=LedgerEntryType.EXPENSE,
                direction=LedgerDirection.OUT,
                amount=Decimal(amount),
                currency="EGP",
                status=LedgerStatus.POSTED,
                method=PaymentMethod.CASH,
                note=label,
                posted_at=datetime.now(UTC) - timedelta(days=idx + 1),
                posted_by=cashier.id,
            )
        )

    accrued_commissions = (
        await session.execute(select(Commission, Policy).join(Policy, Commission.policy_id == Policy.id).order_by(Commission.created_at))
    ).all()
    for idx, (commission, policy) in enumerate(accrued_commissions):
        payee = policy.client.account_owner_id if policy.client is not None else None
        settlement = CommissionSettlement(
            commission_id=commission.id,
            status=SettlementStatus.ACCRUED,
            amount=commission.amount,
            currency="EGP",
            payee_user_id=payee,
            approved_at=datetime.now(UTC) - timedelta(days=4) if idx % 3 == 1 else None,
            paid_at=None,
            method=None,
        )
        if idx % 3 == 1:
            settlement.status = SettlementStatus.APPROVED
            settlement.approved_by = cashier.id
        if idx % 3 == 2:
            # paid last month-to-now, through the bank, with its own outgoing ledger row
            paid_at = datetime.now(UTC) - timedelta(days=2)
            ledger_seq += 1
            entry = LedgerEntry(
                entry_no=f"LE-{ledger_seq:06d}",
                entry_type=LedgerEntryType.COMMISSION_PAYABLE,
                direction=LedgerDirection.OUT,
                amount=commission.amount,
                currency="EGP",
                status=LedgerStatus.POSTED,
                client_id=policy.client_id,
                policy_id=policy.id,
                user_id=payee,
                method=PaymentMethod.BANK,
                note=f"عمولة {policy.policy_no or ''}".strip(),
                posted_at=paid_at,
                posted_by=cashier.id,
            )
            session.add(entry)
            await session.flush()
            settlement.status = SettlementStatus.PAID
            settlement.approved_at = paid_at - timedelta(days=3)
            settlement.approved_by = cashier.id
            settlement.paid_at = paid_at
            settlement.paid_by = cashier.id
            settlement.method = PaymentMethod.BANK
            settlement.ledger_entry_id = entry.id
            commission.status = CommissionStatus.SETTLED
            commission.settled_at = paid_at
        session.add(settlement)

    # the counter continues after the seeded numbers, so a manual posting cannot collide with `entry_no`
    session.add(RequestNumberCounter(key="ledger", last_value=ledger_seq))
    await session.flush()

    # --- portal accounts --------------------------------------------------------------
    owner = User(
        email="owner.mantiq@company.test",
        password_hash=hash_password(PASSWORD),
        full_name="أحمد مالك — شركة منطق",
        role_id=roles["client_owner"].id,
    )
    session.add(owner)
    await session.flush()
    session.add(ClientPortalUser(user_id=owner.id, client_id=clients[0].id, portal_role=PortalRole.OWNER, full_name=owner.full_name))

    # --- leads ------------------------------------------------------------------------
    now = datetime.now(UTC)
    status_cycle = [
        LeadStatus.NEW,
        LeadStatus.CONTACTED,
        LeadStatus.INTERESTED,
        LeadStatus.MEETING_SCHEDULED,
        LeadStatus.QUOTE_SENT,
        LeadStatus.NEGOTIATION,
        LeadStatus.MEETING_DONE,
        LeadStatus.POSTPONED,
        LeadStatus.LOST,
    ]
    for i in range(60):
        rep_email = REPS[i % len(REPS)][0]
        status = status_cycle[i % len(status_cycle)] if i < 45 else LeadStatus.NEW
        follow = now + timedelta(days=RNG.randint(-6, 14))
        lead = Lead(
            type=ClientType.COMPANY if i % 7 else ClientType.INDIVIDUAL,
            name=f"{RNG.choice(COMPANIES)} {i + 1}" if i % 3 else f"فرد {i + 1}",
            contact_name=f"{RNG.choice(['منى', 'كريم', 'هبة', 'طارق'])} {RNG.choice(['عبد الله', 'فتحي', 'صبري'])}",
            mobile=normalize_mobile(f"012{RNG.randint(10000000, 99999999)}"),
            email=f"lead{i}@company.test",
            source=RNG.choice(["إحالة", "واتساب", "معرض", "زيارة ميدانية", "فيسبوك"]),
            insurance_type_id=types[list(types)[i % len(types)]].id,
            headcount=RNG.randint(4, 240),
            current_insurer_id=insurers[list(insurers)[i % len(insurers)]].id,
            current_policy_expiry=(now + timedelta(days=RNG.randint(20, 300))).date(),
            budget=Decimal(RNG.randint(60_000, 800_000)),
            needs_text=RNG.choice(
                [
                    "تحسين التغطية الطبية وإضافة الأسنان",
                    "وثيقة عام للمخازن",
                    "بديل أرخص للوثيقة الحالية",
                    "تغطية جديدة لفرع الإسكندرية",
                ]
            ),
            status=status,
            assigned_to=users[rep_email].id,
            next_followup_at=follow,
        )
        if status is LeadStatus.LOST and lost_reasons:
            lead.lost_reason_id = RNG.choice(lost_reasons).id
            lead.lost_note = "لم يستجب"
        session.add(lead)
        await session.flush()
        for _ in range(RNG.randint(0, 3)):
            session.add(
                LeadActivity(
                    lead_id=lead.id,
                    type=ActivityType.CALL,
                    body="مكالمة تعارف",
                    actor_id=users[rep_email].id,
                    occurred_at=now - timedelta(days=RNG.randint(1, 20)),
                )
            )
    await session.flush()

    # three conversions, exercising the client-list banner
    for name, rep_email in (
        ("منطق للتوزيع", REPS[0][0]),
        ("الأمل للمقاولات", REPS[1][0]),
        ("دار الطباعة", REPS[2][0]),
    ):
        lead = Lead(
            type=ClientType.COMPANY,
            name=name,
            contact_name="مسؤول التعاقد",
            mobile=normalize_mobile(f"0101{RNG.randint(1000000, 9999999)}"),
            email=f"{name[:3]}@company.test",
            source="إحالة",
            insurance_type_id=types[InsuranceTypeCode.MEDICAL.value].id,
            headcount=42,
            status=LeadStatus.WON,
            assigned_to=users[rep_email].id,
            converted_at=now - timedelta(days=RNG.randint(1, 12)),
            converted_by=users[rep_email].id,
            next_followup_at=now + timedelta(days=30),
        )
        session.add(lead)
        await session.flush()
        client = Client(
            type=ClientType.COMPANY,
            name=name,
            status=ClientStatus.ACTIVE,
            account_owner_id=users[rep_email].id,
            source_lead_id=lead.id,
            converted_at=lead.converted_at,
            since_date=(lead.converted_at or now).date(),
        )
        session.add(client)
        await session.flush()
        lead.converted_client_id = client.id
        session.add(ClientContact(client_id=client.id, name="منى مسؤول", title="HR", mobile=lead.mobile, is_primary=True))
        await session.flush()
        session.add(
            ImportJob(
                client_id=client.id,
                type=ImportJobType.INSURED_PERSONS,
                status=ImportJobStatus.COMPLETED,
                filename="employees.xlsx",
                total_rows=12,
                stats={"created": 12, "updated": 0, "rejected": 0},
                error_report=[],
                rows_payload=[],
                created_by=users[rep_email].id,
            )
        )
        for i in range(12):
            session.add(
                InsuredPerson(
                    client_id=client.id,
                    name=f"موظف {name[:6]} {i + 1}",
                    mobile=normalize_mobile(f"015{RNG.randint(100000000, 999999999)}"),
                    department=RNG.choice(["المالية", "الإنتاج", "المخازن"]),
                    coverage_status=CoverageStatus.ACTIVE,
                    coverage_start=date(2025, 6, 1),
                    coverage_end=date(2026, 5, 31),
                )
            )
    await session.flush()

    # --- requests ---------------------------------------------------------------------
    counter = RequestNumberCounter(key="requests", last_value=1000)
    session.add(counter)
    await session.flush()
    subjects = [
        "طلب إضافة موظف جديد",
        "استفسار عن شبكة المستشفيات",
        "خطأ في اسم الموظف بالوثيقة",
        "طلب موافقة مسبقة لعملية",
        "تحديث بطاقة العلاج",
        "استفسار عن تاريخ التجديد",
        "شكوى بطء الرد من شركة التأمين",
    ]
    all_insured = list((await session.execute(select(InsuredPerson).limit(200))).scalars())
    number = counter.last_value
    for i in range(24):
        number += 1
        person = all_insured[i % len(all_insured)] if all_insured else None
        channel = [Channel.WHATSAPP, Channel.CALL, Channel.WHATSAPP, Channel.EMAIL, Channel.MANUAL][i % 5]
        priority = [RequestPriority.NORMAL, RequestPriority.HIGH, RequestPriority.URGENT, RequestPriority.LOW][i % 4]
        created = now - timedelta(hours=RNG.randint(1, 96))
        request_status = [
            RequestStatus.NEW,
            RequestStatus.IN_PROGRESS,
            RequestStatus.REPLIED,
            RequestStatus.RESOLVED,
            RequestStatus.WAITING_INSURER,
        ][i % 5]
        request = ServiceRequest(
            number=number,
            client_id=person.client_id if person else clients[0].id,
            insured_person_id=person.id if person else None,
            insurer_id=insurers[list(insurers)[i % len(insurers)]].id,
            insurance_type_id=types[InsuranceTypeCode.MEDICAL.value].id,
            channel=channel,
            subject=subjects[i % len(subjects)],
            description="يحتاج الموظف إضافة للوثيقة مع صورة بطاقة.",
            contact_name=person.name if person else "زائر",
            contact_mobile=person.mobile if person else None,
            status=request_status,
            priority=priority,
            assigned_to=users["cs"].id,
            created_at=created,
            updated_at=created,
            last_activity_at=created + timedelta(minutes=RNG.randint(5, 400)),
            first_response_at=created + timedelta(minutes=RNG.randint(20, 400))
            if request_status in {RequestStatus.REPLIED, RequestStatus.RESOLVED}
            else None,
            resolved_at=created + timedelta(hours=6) if request_status is RequestStatus.RESOLVED else None,
            is_overdue=(i % 7 == 3),
        )
        counter.last_value = number
        session.add(request)
        await session.flush()
        session.add(
            RequestMessage(
                request_id=request.id,
                direction=MessageDirection.IN,
                channel=channel,
                body=request.description,
                sender_ref="واتساب العميل",
                external_id=f"wamid.seed_{request.number}",
                created_at=created,
            )
        )
        if request.first_response_at:
            session.add(
                RequestMessage(
                    request_id=request.id,
                    direction=MessageDirection.OUT,
                    channel=channel,
                    body="تم إرسال الطلب للشركة وجاري المتابعة.",
                    created_at=request.first_response_at,
                )
            )
        if i % 3 == 0:
            session.add(
                InternalNote(request_id=request.id, author_id=users["cs"].id, body="الموظف أرفق البطاقة later.", created_at=created)
            )
    await session.flush()

    # --- meetings + issuing pipeline (screens 3, 6 and 13 need real rows) ---------------
    meeting_leads = list(
        (
            await session.execute(
                select(Lead)
                .where(Lead.status.in_([LeadStatus.MEETING_SCHEDULED, LeadStatus.NEGOTIATION, LeadStatus.QUOTE_SENT]))
                .order_by(Lead.created_at)
                .limit(9)
            )
        )
        .scalars()
        .all()
    )
    for i, lead in enumerate(meeting_leads):
        session.add(
            LeadMeeting(
                lead_id=lead.id,
                scheduled_at=now + timedelta(days=(i % 4) - 1, hours=2 + i),
                location=RNG.choice(["فرع المعادي", "مكالمة فيديو", "مقر العميل"]),
                created_by=lead.assigned_to,
            )
        )

    policies = list((await session.execute(select(Policy).order_by(Policy.created_at).limit(3))).scalars().all())
    for i, policy in enumerate(policies):
        case = IssuingCase(
            client_id=policy.client_id,
            insurer_id=policy.insurer_id,
            insurance_type_id=policy.insurance_type_id,
            stage=list(IssuingStage)[i % len(list(IssuingStage))],
            assigned_to=users["cs"].id,
            headcount=policy.headcount,
            premium_amount=policy.premium_amount,
            started_at=policy.created_at,
        )
        session.add(case)
        await session.flush()
        for j, doc_name in enumerate(["طلب الاكتتاب", "كشف أسماء الموظفين", "صورة السجل التجاري", "إيصال السداد"]):
            session.add(
                IssuingDocument(
                    case_id=case.id,
                    name=doc_name,
                    status=IssuingDocumentStatus.UPLOADED if (i + j) % 3 else IssuingDocumentStatus.MISSING,
                    updated_by=users["cs"].id,
                )
            )
    await session.flush()

    # CS staff scoped to the first two clients only (shows user_scopes in screen 15)
    session.add(UserScope(user_id=users["cs"].id, scope_type=ScopeType.CLIENT, scope_id=clients[0].id))
    session.add(UserScope(user_id=users["cs"].id, scope_type=ScopeType.CLIENT, scope_id=clients[1].id))
    await session.commit()
    return {
        "users": len(users) + 2,
        "roles": len(roles),
        "insurers": len(insurers),
        "leads": 63,
        "clients": len(clients) + 3,
        "requests": number - 1000,
    }


async def main() -> None:
    async with get_sessionmaker()() as session:
        print(await seed(session))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
