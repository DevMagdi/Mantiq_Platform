#!/usr/bin/env python3
"""Post the *green* run's own numbers to the PR.

Why this exists: `ci-e2e-digest.py` only speaks when something fails, so a passing round left no record of
how many tests actually ran — and "it passes now" is not a measurable claim. Reviewers (and the next round)
need the counts on the good runs too: how many ran, how many were skipped and for which reason, and how the
two Playwright projects split.

The JSON report is produced by every CI run (`playwright.config.ts` always configures the `json` reporter
under `CI`), so this reads exactly what Playwright wrote and nothing else. Exit status is always 0 — a
summary must never be a second reason for a red run.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

REPORT = Path(os.environ.get("PLAYWRIGHT_JSON_OUTPUT_NAME", "/tmp/playwright-results.json"))
OUT = Path(os.environ.get("E2E_SUMMARY_FILE", "/tmp/e2e-summary.md"))


def _specs(suite: dict[str, Any], trail: tuple[str, ...] = (), file: str = "?") -> list[dict[str, Any]]:
    """Flatten suites → one row per (spec, project, status), with the `describe` trail for readability.

    Playwright puts the spec *file* on the outer suite and repeats the describe titles per level, so the file
    is carried down instead of read from the leaf (reading it there produced `?` for every row).
    """
    rows: list[dict[str, Any]] = []
    file = str(suite.get("file") or file)
    title = str(suite.get("title", ""))
    inner = tuple([*trail, *(() if not title or title.endswith(".spec.ts") else (title,))])
    for spec in suite.get("specs", []):
        loc = spec.get("location") or {}
        for test in spec.get("tests", []):
            for result in test.get("results", []):
                rows.append(
                    {
                        "file": str(loc.get("file") or file),
                        "line": loc.get("line", "?"),
                        "title": " › ".join([*inner, str(spec.get("title", ""))]),
                        "project": test.get("projectName") or "?",
                        "status": result.get("status"),
                        "duration": int(result.get("duration") or 0),
                    }
                )
    for child in suite.get("suites", []):
        rows.extend(_specs(child, inner, file))
    return rows


def main() -> int:
    if not REPORT.is_file():
        print(f"summary: no report at {REPORT} — nothing to post", file=sys.stderr)
        return 0
    try:
        data = json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"summary: unreadable report: {exc}", file=sys.stderr)
        return 0

    stats = data.get("stats", {})
    rows: list[dict[str, Any]] = []
    for suite in data.get("suites", []):
        rows.extend(_specs(suite))

    # Playwright reports a test once per result; the last result is the verdict that counted
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        latest[(row["file"], row["title"], row["project"])] = row
    verdicts = list(latest.values())

    def count(predicate) -> int:  # type: ignore[no-untyped-def]
        return sum(1 for row in verdicts if predicate(row))

    skipped = [row for row in verdicts if row["status"] == "skipped"]
    passed = [row for row in verdicts if row["status"] in {"passed", "expected"}]
    failed = [row for row in verdicts if row["status"] in {"failed", "timedOut", "interrupted", "unexpected"}]
    projects = sorted({row["project"] for row in verdicts})
    slowest = sorted(passed, key=lambda row: row["duration"], reverse=True)[:5]
    seconds = sum(row["duration"] for row in verdicts) / 1000.0

    body = [
        "### e2e · الأرقام من تقرير Playwright نفسه (لا من العدد المتوقَّع)",
        "",
        f"**{len(verdicts)} اختباراً** · {len(passed)} ناجح · **{len(failed)} فاشل** · {len(skipped)} مُتخطٍّ "
        f"· `stats`: expected={stats.get('expected')} unexpected={stats.get('unexpected')} "
        f"skipped={stats.get('skipped')} flaky={stats.get('flaky')} · مجموع الزمن {seconds:.0f}s",
        "",
        "| المشروع | ناجح | مُتخطٍّ | فاشل |",
        "|---|---|---|---|",
    ]
    for project in projects:
        body.append(
            f"| `{project}` | {count(lambda r, p=project: r['project'] == p and r['status'] in {'passed', 'expected'})} "
            f"| {count(lambda r, p=project: r['project'] == p and r['status'] == 'skipped')} "
            f"| {count(lambda r, p=project: r['project'] == p and r['status'] in {'failed', 'timedOut', 'interrupted', 'unexpected'})} |"
        )

    if skipped:
        reasons: dict[str, int] = {}
        for row in skipped:
            key = f"{row['file']} · {row['title']}"
            reasons[key] = reasons.get(key, 0) + 1
        body += ["", "<details><summary>التخطيات — كل واحدة باسم اختبارها (ADR-018/019 تشرح السبب)</summary>", ""]
        for key, times in sorted(reasons.items()):
            body.append(f"- `{key}` ×{times}")
        body += ["", "</details>"]

    if slowest:
        body += ["", "<details><summary>أبطأ خمسة اختبارات ناجحة</summary>", ""]
        for row in slowest:
            body.append(f"- {row['duration'] / 1000:.1f}s · `{row['file']}` · {row['title']}")
        body += ["", "</details>"]

    text = "\n".join(body)
    OUT.write_text(text, encoding="utf-8")
    print(f"summary: {len(verdicts)} tests → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
