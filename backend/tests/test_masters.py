"""Reference data: the insurer list is loaded from CSV and must be idempotent (spec §5)."""

from __future__ import annotations

from tests.helpers import login

ADMIN = "admin@brokerhub.test"
MANAGER = "manager@brokerhub.test"

CSV = "code,name,name_en\nmediya,ميديا,Medya\naxa,أكسا,AXA\n,\nbroken,,\n".encode()


async def test_insurers_are_listed_with_both_names(client) -> None:
    async with login(client, MANAGER) as h:
        response = await client.get("/api/v1/insurers", headers=h)
        assert response.status_code == 200
        rows = response.json()
        assert {r["code"] for r in rows} == {"axa", "zalako"}
        assert all(r["is_active"] for r in rows)
        assert {"id", "code", "name", "name_en", "is_active"} <= set(rows[0])


async def test_csv_import_upserts_and_never_deletes(client) -> None:
    async with login(client, ADMIN) as h:
        imported = await client.post(
            "/api/v1/insurers/import-csv",
            headers=h,
            files={"file": ("insurers.csv", CSV, "text/csv")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json() == {"created": 1, "updated": 1, "skipped": 2}

        rows = (await client.get("/api/v1/insurers", headers=h)).json()
        mediya = next(r for r in rows if r["code"] == "mediya")
        assert mediya["name_en"] == "Medya"

        # running the same file again is a no-op on the count side: it updates, never duplicates
        again = await client.post("/api/v1/insurers/import-csv", headers=h, files={"file": ("insurers.csv", CSV, "text/csv")})
        assert again.json()["created"] == 0
        assert again.json()["updated"] == 2
        assert len((await client.get("/api/v1/insurers", headers=h)).json()) == 3

        # master data belongs to managers and up; a rep may not write it
        async with login(client, "rep_a@brokerhub.test") as rh:
            refused = await client.post("/api/v1/insurers/import-csv", headers=rh, files={"file": ("x.csv", CSV, "text/csv")})
            assert refused.status_code == 403


async def test_csv_without_the_code_column_is_a_validation_error(client) -> None:
    async with login(client, ADMIN) as h:
        bad = await client.post(
            "/api/v1/insurers/import-csv",
            headers=h,
            files={"file": ("insurers.csv", b"company,count\nA,1\n", "text/csv")},
        )
        assert bad.status_code == 422
        assert bad.json()["code"] == "validation_failed"

        binary = await client.post(
            "/api/v1/insurers/import-csv",
            headers=h,
            files={"file": ("insurers.csv", b"\xff\xfe\x00\x01not-utf8", "text/csv")},
        )
        assert binary.status_code == 422


async def test_types_reasons_and_sla_matrix(client) -> None:
    async with login(client, MANAGER) as h:
        types = (await client.get("/api/v1/insurance-types", headers=h)).json()
        assert {t["code"] for t in types} == {"medical"}
        assert all(t["name"] for t in types)

        reasons = (await client.get("/api/v1/lost-reasons", headers=h)).json()
        assert [r["name"] for r in reasons] == ["سعر أعلى"]

        matrix = (await client.get("/api/v1/sla-policies", headers=h)).json()
        assert len(matrix) == 4
        by_priority = {row["priority"]: row for row in matrix}
        assert by_priority["urgent"]["first_response_minutes"] == 30
        assert by_priority["normal"]["resolution_minutes"] == 1440
        # a lower priority may never be faster than a higher one
        assert by_priority["low"]["first_response_minutes"] > by_priority["urgent"]["first_response_minutes"]
