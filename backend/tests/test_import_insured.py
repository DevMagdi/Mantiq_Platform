"""Insured import: dry-run → confirm → report, and row-level errors (spec §9.11)."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook

from tests.helpers import login

MANAGER = "manager@brokerhub.test"
REP_A = "rep_a@brokerhub.test"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook(rows: list[list[Any]], header: tuple[str, ...] = ("الاسم", "الموبايل", "القسم")) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _upload(client: Any, auth: dict[str, str], client_id: str, content: bytes, filename: str = "insured.xlsx"):
    return await client.post(
        f"/api/v1/clients/{client_id}/insured/import",
        files={"file": (filename, content, XLSX_MIME)},
        headers=auth,
    )


async def test_dry_run_then_commit_creates_rows(client, ids: dict[str, dict[str, str]]) -> None:
    content = _workbook(
        [
            ["منى فؤاد", "01001112233", "الإنتاج"],
            ["عصام نجيب", "01004445566", "الصيانة"],
            ["صفوت", "not-a-phone", None],  # unusable mobile => rejected (mobile is the dedupe key)
            ["", "01007778899", "المخازن"],  # no name => rejected
        ]
    )
    async with login(client, MANAGER) as auth:
        preview = await _upload(client, auth, ids["clients"]["ألف"], content)
        assert preview.status_code == 201, preview.text
        job = preview.json()
        assert job["status"] == "dry_run"
        assert job["confirm_required"] is True
        assert job["total_rows"] == 4
        assert job["stats"]["will_create"] == 2
        assert job["stats"]["rejected"] == 2
        assert job["preview"][0]["mobile"] == "+201001112233"
        reported = {(row["row"], row["field"]) for row in job["error_report"]}
        assert (4, "mobile") in reported and (5, "name") in reported

        # nothing was written yet — the import is two-phase on purpose
        before = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/insured", headers=auth)
        assert before.json()["kpis"]["total"] == 0

        committed = await client.post(f"/api/v1/import-jobs/{job['id']}/commit", headers=auth)
        assert committed.status_code == 200, committed.text
        after = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/insured", headers=auth)

    assert after.json()["kpis"]["total"] == 2
    names = {row["name"] for row in after.json()["page"]["items"]}
    assert {"منى فؤاد", "عصام نجيب"} == names


async def test_commit_is_idempotent_per_job(client, ids: dict[str, dict[str, str]]) -> None:
    content = _workbook([["طارق سليم", "01066667788", "المبيعات"]])
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, ids["clients"]["ألف"], content)).json()
        first = await client.post(f"/api/v1/import-jobs/{job['id']}/commit", headers=auth)
        second = await client.post(f"/api/v1/import-jobs/{job['id']}/commit", headers=auth)
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


async def test_reupload_updates_instead_of_duplicating(client, ids: dict[str, dict[str, str]]) -> None:
    rows = [["منى فؤاد", "01001112233", "الإنتاج"]]
    async with login(client, MANAGER) as auth:
        job = (await _upload(client, auth, ids["clients"]["ألف"], _workbook(rows))).json()
        await client.post(f"/api/v1/import-jobs/{job['id']}/commit", headers=auth)

        again = (await _upload(client, auth, ids["clients"]["ألف"], _workbook([["منى فؤاد", "01001112233", "الجودة"]]))).json()
        assert again["stats"]["will_update"] == 1
        assert again["stats"]["will_create"] == 0
        assert again["stats"]["rejected"] == 0
        await client.post(f"/api/v1/import-jobs/{again['id']}/commit", headers=auth)
        listed = await client.get(f"/api/v1/clients/{ids['clients']['ألف']}/insured", headers=auth)
    items = listed.json()["page"]["items"]
    assert len(items) == 1
    assert items[0]["department"] == "الجودة"


async def test_non_excel_and_wrong_header_are_rejected(client, ids: dict[str, dict[str, str]]) -> None:
    async with login(client, MANAGER) as auth:
        csv = await _upload(client, auth, ids["clients"]["ألف"], b"name,mobile\nx,0100\n", filename="insured.csv")
        bad_header = await _upload(client, auth, ids["clients"]["ألف"], _workbook([["x"]], header=("foo", "bar", "baz")))
    assert csv.status_code == 422 and bad_header.status_code == 422
    assert bad_header.json()["code"] == "validation_failed"
    assert "name" in bad_header.json()["message"]
    # the response tells the user which columns were actually found
    assert bad_header.json()["details"]["header"] == ["foo", "bar", "baz"]
    assert csv.json()["details"]["filename"] == "insured.csv"


async def test_rep_of_other_client_cannot_import(client, ids: dict[str, dict[str, str]]) -> None:
    """Out-of-scope client id => 404, even for an upload (spec §6)."""
    content = _workbook([["س", "01001110000", None]])
    async with login(client, REP_A) as auth:
        response = await _upload(client, auth, ids["clients"]["باء"], content)
    assert response.status_code == 404
