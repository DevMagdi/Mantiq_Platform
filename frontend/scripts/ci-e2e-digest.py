#!/usr/bin/env python3
"""Turn a Playwright JSON report into one PR comment that lists every real failure.

Why this exists: GitHub keeps at most 50 annotations per check-run, and the raw job log is served from
`*.logs.github.com`/blob storage that CI-unaware sandboxes cannot read. So "25 failed" was all anyone could
see from the outside, and two specs were fixed by guessing at the DOM instead of reading the error. This
script puts the actual assertion text — locator, expected, received, and the page snapshot when Playwright
captured one — where it can be read: in the PR thread.

Exit status is always 0. A digest is a courtesy; it must never turn a red run red for a second reason.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

MAX_FAILURES = 40  # a comment body is capped at 65 536 chars by GitHub; this stays well inside
MAX_MESSAGE = 2_600


def _one_line(text: str, limit: int) -> str:
    """Playwright messages are ANSI-flecked multi-line dumps.

    The head of the message carries the locator and the expectation; the *tail* carries the call log — the
    lines that say which element intercepted the pointer or why the scroll never settled. Cutting only the
    head is what made three actionability timeouts un diagnosable from the digest, so the budget is split:
    about two thirds from the top, the rest from the bottom, and the cut is marked.
    """
    clean = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
    lines = [line.rstrip() for line in clean.splitlines() if line.strip()]
    if sum(len(line) for line in lines) <= limit:
        return "\n".join(lines) if lines else "(empty)"
    head_budget = int(limit * 0.66)
    head: list[str] = []
    used = 0
    index = 0
    for index, line in enumerate(lines):
        if used + len(line) > head_budget:
            break
        head.append(line)
        used += len(line)
    tail: list[str] = []
    used = limit - used
    for line in reversed(lines[index:]):
        if used + len(line) > limit:
            break
        tail.insert(0, line)
        used += len(line)
    joined = "\n".join([*head, "… (منتصف الرسالة مقصوص — السطر التالية من ذيل الـ call log) …", *tail])
    return joined if joined else "(empty)"


def _walk(suite: dict[str, Any], trail: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Flatten suites/specs into the specs that failed, keeping the `describe` names (not the file name,
    which the JSON repeats as a suite title and which the table above already shows)."""
    found: list[dict[str, Any]] = []
    title = str(suite.get("title", ""))
    inner = tuple(sorted(set([*trail, *(() if title.endswith(".spec.ts") or not title else (title,))])))
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            for result in test.get("results", []):
                if result.get("status") in ("failed", "timedOut", "interrupted"):
                    errors = [str(e.get("message") or "") for e in result.get("errors", [])]
                    snippet = next((e for e in errors if "Error:" in e or "expect(" in e), errors[0] if errors else "")
                    loc = spec.get("location") or {}
                    found.append(
                        {
                            "file": str(loc.get("file") or title or "?"),
                            "line": loc.get("line", "?"),
                            "title": " › ".join([*inner, str(spec.get("title", ""))]),
                            "project": test.get("projectName") or result.get("projectName") or "?",
                            "message": snippet or str(result.get("error", {}).get("message", "")),
                            "duration": result.get("duration", 0),
                        }
                    )
    for child in suite.get("suites", []):
        found.extend(_walk(child, inner))
    return found


def _api_log_excerpt(limit: int = 34) -> list[str]:
    """The API's own tail, because "login failed" plus a 30 s wait names a symptom, not a cause.

    `playwright.config.ts` runs uvicorn through `tee /tmp/e2e-api.log`. A stall shows up here as either a
    missing line, a 5xx, or a request whose `ms=` is seconds long — three very different bugs that a
    screenshot cannot tell apart.
    """
    path = Path(os.environ.get("E2E_API_LOG", "/tmp/e2e-api.log"))
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    # keep the interesting lines (errors, slow requests, tracebacks) over the happy-path chatter
    interesting = [
        line
        for line in lines
        if "status=5" in line or "status=429" in line or "Traceback" in line or "Error" in line or _slow(line)
    ]
    tail = interesting[-limit:] if interesting else lines[-limit:]
    if not tail:
        return []
    for index, line in enumerate(tail):
        tail[index] = "".join(ch for ch in line if ch == "\t" or ord(ch) >= 32)[:400]
    return [
        "",
        "<details><summary>سجلّ الـAPI كما رآه الخادم (أسطر الأخطاء والطلبات البطيئة)</summary>",
        "",
        "```text",
        *tail,
        "```",
        "</details>",
        "",
    ]


def _slow(line: str) -> bool:
    """`ms=1234.5` — a request that took over a second is a stall worth showing, not noise."""
    marker = "ms="
    start = line.find(marker)
    if start < 0:
        return False
    value = line[start + len(marker) :].split()[0] if len(line) > start + len(marker) else ""
    try:
        return float(value.rstrip(".,;")) >= 1000
    except ValueError:
        return False

def main() -> int:
    report = Path(os.environ.get("PLAYWRIGHT_JSON_OUTPUT_NAME", "/tmp/playwright-results.json"))
    if not report.is_file():
        print(f"digest: no report at {report} — nothing to post", file=sys.stderr)
        return 0
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"digest: unreadable report: {exc}", file=sys.stderr)
        return 0

    failures: list[dict[str, Any]] = []
    for suite in data.get("suites", []):
        failures.extend(_walk(suite))

    stats = data.get("stats", {})
    by_file: dict[str, int] = {}
    for item in failures:
        by_file[item["file"]] = by_file.get(item["file"], 0) + 1

    body = [
        "### فشل e2e كما هو في التقرير، لا كما يُستنتَج من 50 ملاحظة",
        "",
        f"**{stats.get('unexpected', len(failures))} غير متوقع** · {stats.get('expected', 0)} ناجح · "
        f"{stats.get('skipped', 0)} مُتخطٍّ · {stats.get('flaky', 0)} متذبذب",
        "",
        "| الملف | مرات الفشل |",
        "|---|---|",
    ]
    for name, count in sorted(by_file.items(), key=lambda kv: (-kv[1], kv[0])):
        body.append(f"| `{name}` | {count} |")
    body.append("")

    # the per-failure budget shrinks as the list grows, so a bad round keeps its header table *and* the
    # call-log tails of every failure instead of losing the last ones to the 60 000-character cap
    shown = failures[:MAX_FAILURES]
    # 34 000 of message budget + ~250 chars of heading per failure keeps the whole comment inside the cap
    per_failure = max(700, min(MAX_MESSAGE, 34_000 // max(1, len(shown))))
    for item in shown:
        body.append(f"**`{item['file']}:{item['line']}` · {item['title']}**  ")
        body.append(f"_project: `{item['project']}` · {item['duration']} ms_")
        body.append("")
        body.append("```text")
        body.append(_one_line(item["message"], per_failure))
        body.append("```")
        body.append("")
    if len(failures) > MAX_FAILURES:
        body.append(f"_… و{len(failures) - MAX_FAILURES} فشلًا آخر مقطوعًا من هذا الملخص._")

    body.extend(_api_log_excerpt())

    out = Path(os.environ.get("E2E_DIGEST_FILE", "/tmp/e2e-failures.md"))
    text = "\n".join(body)
    # GitHub's hard limit is 65 536 chars for a comment body; truncate the tail, never the header table.
    if len(text) > 60_000:
        text = text[:60_000] + "\n\n_(the digest was cut at 60 000 characters)_\n"
    out.write_text(text, encoding="utf-8")
    print(f"digest: {len(failures)} failures → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
