"""Excel/CSV import of the lead pool: preview saves nothing, commit saves everything (spec: team round).

The file is deliberately built in-test rather than read from a fixture: the point is the *mapping*
(Arabic/English headers → the real columns), and a binary fixture hides it.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from app.core.db import get_sessionmaker
from app.models.leads import Lead
from openpyxl import Workbook
from sqlalchemy import func, select

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"
ADMIN = "admin@brokerhub.test"
#: +201000000001 belongs to rep_a's lead in the fixture graph (`conftest.bootstrap`)
TAKEN_MOBILE = "01000000001"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

HEADER = ("الاسم", "الموبايل", "الشركة", "البوزيشن", "البريد", "المصدر", "نوع التأمين", "عدد الأفراد", "تاريخ المتابعة", "البياع المسؤول")


def _workbook(rows: list[list[Any]], header: tuple[str, ...] = HEADER) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _upload(client: Any, auth: dict[str, str], content: bytes, filename: str = "leads.xlsx") -> Any:
    return await client.post("/api/v1/leads/import", files={"file": (filename, content, XLSX_MIME)}, headers=auth)


async def _lead_count() -> int:
    async with get_sessionmaker()() as session:
        return int((await session.execute(select(func.count(Lead.id)))).scalar_one())


async def test_preview_changes_nothing_in_the_database(client) -> None:
    """The dry-run promise: a job row and no leads, whatever the sheet says."""
    before = await _lead_count()
    content = _workbook([["منى فؤاد", "01001112233", "شركة النيل", "المديرة المالية", None, None, None, 40, "2026-11-05", None]])
    async with login(client, MANAGER) as auth:
        preview = await _upload(client, auth, content)
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["status"] == "dry_run"
        assert job["confirm_required"] is True
        assert job["stats"]["rows"] == 1
        assert job["stats"]["ready"] == 1
        assert job["commit_url"] == f"/api/v1/leads/import/{job['id']}/commit"
    assert await _lead_count() == before


async def test_commit_writes_real_leads_with_every_mapped_column(client, ids: dict[str, dict[str, str]]) -> None:
    content = _workbook(
        [
            # company row: name → company, person → contact pair, everything else beside it
            ["منى فؤاد", "01001112233", "شركة النيل", "المديرة المالية", "mona@nile.test", None, "طبي", 40, "05/11/2026", "Rep A"],
            # individual row: no company, Arabic-Indic digits in both number columns, no rep → the pool
            ["عصام نجيب", "٠١٠٠٢٢٢٣٣٣٣", None, None, None, "معرض", None, "١٢", None, None],
        ]
    )
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, content)).json()
        assert job["stats"] == {**job["stats"], "rows": 2, "ready": 2, "errors": 0, "assigned": 1, "unassigned": 1}
        committed = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert committed.status_code == 200, committed.text
        report = committed.json()
        assert report["status"] == "completed"
        assert report["stats"]["created"] == 2
        created = report["stats"]["created"]

        listed = (await client.get("/api/v1/leads?q=النيل", headers=auth)).json()["items"]
        assert len(listed) == 1
        lead = await client.get(f"/api/v1/leads/{listed[0]['id']}", headers=auth)
        row = lead.json()
        assert created == 2
        assert row["name"] == "شركة النيل"
        assert row["type"] == "company"
        assert row["status"] == "new"
        assert row["mobile"] == "+201001112233"
        assert row["contact_name"] == "منى فؤاد"
        assert row["contact_title"] == "المديرة المالية"
        assert row["email"] == "mona@nile.test"
        assert row["headcount"] == 40
        assert row["insurance_type"] == "طبي"
        assert row["assignee_name"] == "Rep A"
        # a date-only cell means that day, not "today" — see `_followup_at`; the serialisation is not the claim
        assert str(row["next_followup_at"]).startswith("2026-11-05"), row["next_followup_at"]
        assert row["source"] == "استيراد Excel"

        listed = (await client.get("/api/v1/leads?q=عصام", headers=auth)).json()["items"]
        assert [row["name"] for row in listed] == ["عصام نجيب"]
        assert listed[0]["type"] == "individual"
        assert listed[0]["assignee_name"] is None  # bullet 6: no rep named means the manager's pool
        # `source`/`headcount` live on the detail payload, not on a list row — assert them where they are
        detail = (await client.get(f"/api/v1/leads/{listed[0]['id']}", headers=auth)).json()
        assert detail["mobile"] == "+201002223333"
        assert detail["headcount"] == 12
        assert detail["source"] == "معرض"


async def test_duplicate_and_broken_rows_are_reported_not_created(client) -> None:
    content = _workbook(
        [
            ["مسروق", TAKEN_MOBILE, None, None, None, None, None, None, None, None],  # rep_a already has it
            ["مكرر", TAKEN_MOBILE, None, None, None, None, None, None, None, None],  # …twice in this file
            ["بريد سيء", "01003334455", None, None, "mona-at-nile", None, None, None, None, None],
            ["تاريخ سيء", "01005556677", None, None, None, None, None, None, "غدًا", None],
            ["بلا موبايل", None, None, None, None, None, None, None, None, None],
            ["سليم", "01006667788", None, None, None, None, "غامض", None, None, "شخص غير موجود"],
        ]
    )
    async with login(client, MANAGER) as auth:
        before = await _lead_count()
        job = (await _upload(client, auth, content)).json()
        # 6 rows: 1 survives, 3 are wrong, 2 are duplicates that get skipped, not failed
        assert job["stats"]["ready"] == 1, job["stats"]
        assert job["stats"]["errors"] == 3
        assert job["stats"]["skipped_duplicate"] == 2
        fields = {entry["field"] for entry in job["error_report"] if entry["kind"] == "error"}
        assert fields == {"email", "followup", "mobile"}
        # the last row is *kept* but says what it could not honour
        assert job["stats"]["warnings"] == 2
        notes = [entry["message"] for entry in job["error_report"] if entry["kind"] == "warning"]
        assert any("غامض" in note for note in notes)
        assert any("شخص غير موجود" in note for note in notes)

        committed = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        report = committed.json()
        assert report["stats"]["created"] == 1
        assert report["stats"]["skipped_duplicate"] == 2
        assert await _lead_count() == before + 1


async def test_committing_twice_is_refused(client) -> None:
    content = _workbook([["وحيد", "01007778899", None, None, None, None, None, None, None, None]])
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, content)).json()
        first = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert first.status_code == 200
        again = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert again.status_code == 409, again.text
        assert "completed" in again.json()["message"]


async def test_a_rep_may_not_import(client) -> None:
    content = _workbook([["ليد لمدير", "01008889900", None, None, None, None, None, None, None, None]])
    async with login(client, REP_A) as auth:
        refused = await _upload(client, auth, content)
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "forbidden"
    assert await _lead_count() == 2  # the fixture's two leads, nothing more


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        # CSV goes through the same mapping, with English headers this time
        (
            "leads.csv",
            "Name,Mobile,Company,Follow-up\nسامح,01009998877,الدلتا,2026-12-01\n".encode(),
            "سامح",
        ),
        # a BOM and a semicolon delimiter are both things real exports do
        ("leads.csv", "الاسم;الموبايل\nهالة;01001212121\n".encode("utf-8-sig"), "هالة"),
    ],
)
async def test_csv_files_are_accepted(client, filename: str, content: bytes, expected: str) -> None:
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, content, filename=filename)).json()
        assert job["stats"]["ready"] == 1, job["error_report"]
        committed = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert committed.json()["stats"]["created"] == 1
        # the company column owns `name` (that is the account the pipeline lists), so `expected` is the person
        found = (await client.get(f"/api/v1/leads?q={expected}", headers=auth)).json()["items"]
        assert [row["contact_name"] or row["name"] for row in found] == [expected]


async def test_unreadable_files_are_422_with_a_reason_never_a_500(client) -> None:
    async with login(client, MANAGER) as auth:
        not_excel = await _upload(client, auth, b"this is plainly text, not a workbook", filename="leads.xlsx")
        assert not_excel.status_code == 422, not_excel.text
        assert "xlsx" in not_excel.json()["message"]

        empty = await _upload(client, auth, b"", filename="leads.xlsx")
        assert empty.status_code == 422

        no_name_column = await _upload(client, auth, _workbook([["x"]], header=("لا شيء",)), filename="leads.xlsx")
        assert no_name_column.status_code == 422
        message = no_name_column.json()["message"]
        assert "name" in message.lower() or "الاسم" in message


async def test_the_report_survives_a_reload_and_names_its_author(client) -> None:
    content = _workbook([["خالد", "01002223344", None, None, None, None, None, None, None, None]])
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, content)).json()
        await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        again = await client.get(f"/api/v1/leads/import/{job['id']}", headers=auth)
        assert again.status_code == 200
        assert again.json()["status"] == "completed"
        assert again.json()["committed_at"] is not None
        assert again.json()["total_rows"] == 1

    unknown = await client.get(f"/api/v1/leads/import/{'0' * 32}", headers=auth)
    assert unknown.status_code == 404
    assert unknown.json()["message"] == "Import job not found", "the 404 has to say which thing was missing"


async def test_the_commit_is_audited_and_the_pool_stays_scoped(client) -> None:
    """Who imported what is in `audit_logs`; the unassigned pool a manager sees is not what a rep sees."""
    from app.models.requests import AuditLog

    content = _workbook([["وارد", "01004445599", "شركة الوارد", None, None, None, None, None, None, None]])
    async with login(client, ADMIN) as auth:
        job = (await _upload(client, auth, content)).json()
        await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        unassigned = (await client.get("/api/v1/leads?unassigned=true", headers=auth)).json()
        assert any(row["name"] == "شركة الوارد" for row in unassigned["items"])

    async with get_sessionmaker()() as session:
        entry = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "lead_import.committed").order_by(AuditLog.occurred_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert entry is not None, "the commit wrote no audit row"
        assert entry.after["filename"] == "leads.xlsx"
        assert entry.after["created"] == 1

    async with login(client, REP_A) as rep:
        mine = (await client.get("/api/v1/leads?unassigned=true", headers=rep)).json()
        assert all(row["name"] != "شركة الوارد" for row in mine["items"])


async def test_an_unknown_rep_name_is_a_warning_and_the_row_stays_unassigned(client) -> None:
    """The «البياع المسؤول» column is a convenience, not a foreign key: an unknown name must not invent an owner."""
    async with login(client, MANAGER) as auth:
        sheet = _csv(("الاسم", "الموبايل", "البياع المسؤول"), [("صف بلا بياع", "01099887766", "بياع غير موجود")])
        preview = await client.post("/api/v1/leads/import", headers=auth, files={"file": ("leads.csv", sheet, "text/csv")})
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["stats"]["ready"] == 1
        assert job["stats"]["assigned"] == 0
        warnings = [entry for entry in job["error_report"] if entry["kind"] == "warning"]
        assert any("لا بياع باسم" in entry["message"] for entry in warnings), job["error_report"]

        commit = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert commit.status_code == 200, commit.text
        found = (await client.get("/api/v1/leads?q=صف بلا بياع", headers=auth)).json()
        assert found["total"] == 1
        assert found["items"][0]["assignee_id"] is None


async def test_a_named_rep_column_assigns_the_lead_to_that_rep(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        sheet = _csv(("الاسم", "الموبايل", "البياع"), [("صف لريب A", "01099887755", "Rep A")])
        preview = await client.post("/api/v1/leads/import", headers=auth, files={"file": ("leads.csv", sheet, "text/csv")})
        job = preview.json()
        assert job["stats"]["assigned"] == 1, job["stats"]
        commit = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert commit.status_code == 200, commit.text
    async with login(client, REP_A) as rep:
        mine = (await client.get("/api/v1/leads?q=صف لريب A", headers=rep)).json()
        assert mine["total"] == 1
        assert mine["items"][0]["assignee_id"] == ids["users"]["rep_a"]


async def test_a_quoted_arabic_name_keeps_its_comma_and_utf8(client) -> None:
    """Arabic + a comma inside quotes + a BOM: the three ways a real export breaks a naive parser."""
    async with login(client, MANAGER) as auth:
        body = "\ufeff" + 'الاسم,الموبايل\n"شركة النيل, فرع الجيزة",01099887744\n'
        preview = await client.post(
            "/api/v1/leads/import",
            headers=auth,
            files={"file": ("leads.csv", body.encode("utf-8"), "text/csv")},
        )
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["stats"]["ready"] == 1
        assert job["preview"][0]["name"] == "شركة النيل, فرع الجيزة"
        commit = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert commit.status_code == 200, commit.text
        found = (await client.get("/api/v1/leads?q=فرع الجيزة", headers=auth)).json()
        assert found["total"] == 1
        assert "فرع الجيزة" in found["items"][0]["name"]


async def test_a_file_over_the_row_limit_is_refused_before_anything_is_written(client) -> None:
    async with login(client, MANAGER) as auth:
        lines = ["الاسم,الموبايل"] + [f"صف {n},01099{n:06d}" for n in range(5_001)]
        response = await client.post(
            "/api/v1/leads/import",
            headers=auth,
            files={"file": ("big.csv", "\n".join(lines).encode("utf-8"), "text/csv")},
        )
        assert response.status_code == 422, response.text
        assert response.json()["details"]["limit"] == 5000
        assert (await client.get("/api/v1/leads?q=صف 1&page_size=5", headers=auth)).json()["total"] == 0


async def test_a_commit_is_refused_when_the_job_is_not_a_preview(client) -> None:
    """`committed` and `failed` both answer 409 with the status in the payload — never a silent second file."""
    async with login(client, MANAGER) as auth:
        sheet = _csv(("الاسم", "الموبايل"), [("صف ينفذ مرة", "01099887733")])
        job = (await client.post("/api/v1/leads/import", headers=auth, files={"file": ("leads.csv", sheet, "text/csv")})).json()
        assert (await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)).status_code == 200
        again = await client.post(f"/api/v1/leads/import/{job['id']}/commit", headers=auth)
        assert again.status_code == 409, again.text
        assert again.json()["details"]["status"] == "completed"
        assert (await client.get("/api/v1/leads?q=صف ينفذ مرة", headers=auth)).json()["total"] == 1


async def test_the_arabic_email_header_is_read_and_a_bad_address_is_a_row_error(client) -> None:
    """
    «الإيميل» / «ايميل» / «الايميل» are one column: they differ by the definite article and by how the hamza
    was typed, and a broker's export uses all three. The alias list only spelled «البريد» and «البريد
    الإلكتروني», so an «الإيميل» column matched nothing, the whole column disappeared, and a row whose address
    was `not-an-email` was counted **ready** and imported (R3, `team-round.spec.ts`). Headers are folded
    through `_norm_header` now — article, hamza and ta-marbuta — so no spelling can silently drop a column.
    """
    async with login(client, MANAGER) as auth:
        sheet = _csv(
            ("الاسم", "الموبايل", "الإيميل"),
            [("صف بعنوان صحيح", "01099887711", "ok@example.test"), ("صف بعنوان فاسد", "01099887712", "not-an-email")],
        )
        preview = await client.post("/api/v1/leads/import", headers=auth, files={"file": ("leads.csv", sheet, "text/csv")})
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["stats"]["ready"] == 1, job["stats"]
        assert job["stats"]["errors"] == 1, job["stats"]
        errors = [entry for entry in job["error_report"] if entry["kind"] == "error"]
        assert errors[0]["field"] == "email", errors
        assert errors[0]["row"] == 3, errors


async def test_a_column_nobody_recognises_is_reported_instead_of_vanishing(client) -> None:
    """A header the mapper does not know is carried back as a warning on row 1 — silence is what hid the bug above."""
    async with login(client, MANAGER) as auth:
        sheet = _csv(("الاسم", "الموبايل", "الملاحظات السرية"), [("صف بعمود غريب", "01099887721", "شيء ما")])
        preview = await client.post("/api/v1/leads/import", headers=auth, files={"file": ("leads.csv", sheet, "text/csv")})
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["stats"]["ready"] == 1
        warnings = [entry for entry in job["error_report"] if entry["kind"] == "warning"]
        assert warnings and warnings[0]["field"] == "header", job["error_report"]
        assert "الملاحظات السرية" in warnings[0]["message"]
        assert job["stats"]["warnings"] == 1


def _csv(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> bytes:
    """A UTF-8 CSV built in-test, matching `_workbook`'s contract (bytes + a real filename)."""
    import csv as _csv_module
    import io as _io

    buffer = _io.StringIO()
    writer = _csv_module.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")
