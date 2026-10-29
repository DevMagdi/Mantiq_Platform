"""Turn a failed `pytest` run into a PR comment (the log itself is not readable from a reviewer's sandbox).

Why this exists: GitHub serves the raw job log from blob storage, which a sandboxed reviewer cannot fetch,
and check-run annotations are capped at 50 and carry no traceback. R5 lost a whole round to "backend is red"
with no way to see which of 234 tests failed. This script reads the log the step already wrote to
`/tmp/pytest.log`, prints the short summary and the first few tracebacks (trimmed), and writes markdown.

It never fails the run: the caller passes `continue-on-error`.

    python scripts/ci-pytest-digest.py            # writes /tmp/pytest-failures.md
"""

from __future__ import annotations

import pathlib
import re
import sys

LOG = pathlib.Path("/tmp/pytest.log")
OUT = pathlib.Path("/tmp/pytest-failures.md")
MAX_BLOCK_LINES = 40
MAX_BLOCKS = 4


def _sections(text: str) -> tuple[list[str], str]:
    """Split the log into traceback blocks and keep the `short test summary` verbatim."""
    lines = text.splitlines()
    summary_at = next((index for index, line in enumerate(lines) if re.match(r"^=+ short test summary", line)), None)
    body = lines[:summary_at] if summary_at is not None else lines
    summary = "\n".join(lines[summary_at:]) if summary_at is not None else ""

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if re.match(r"^_{3,} .* _{3,}$", line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block) for block in blocks], summary


def main() -> int:
    if not LOG.exists():
        OUT.write_text("## pytest failed\n\nThe job produced no `/tmp/pytest.log` — the failure was before pytest.\n", encoding="utf-8")
        print(f"wrote {OUT} (no log)")
        return 0
    text = LOG.read_text(encoding="utf-8", errors="replace")
    blocks, summary = _sections(text)

    counts = re.findall(r"^=+ (.*?(?:passed|failed|error).*?) =+$", text, flags=re.MULTILINE)
    parts = ["## pytest failed", ""]
    if counts:
        parts += [f"**{counts[-1].strip()}**", ""]
    if summary:
        parts += ["```", "\n".join(summary.splitlines()[:40]), "```", ""]
    for block in blocks[:MAX_BLOCKS]:
        trimmed = block.splitlines()
        if len(trimmed) > MAX_BLOCK_LINES:
            trimmed = [*trimmed[:20], "…", *trimmed[-19:]]
        parts += ["```", "\n".join(trimmed), "```", ""]
    if len(blocks) > MAX_BLOCKS:
        parts.append(f"_({len(blocks) - MAX_BLOCKS} more failing test(s) not shown — see the job's annotations.)_")

    OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(blocks)} failing test block(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
