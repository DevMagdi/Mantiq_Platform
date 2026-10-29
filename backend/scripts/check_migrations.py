"""CI guard (spec §13): a migration that is already on `main` must never be edited.

New revisions are always fine; rewriting history is not. Falls back to "no baseline yet" when
`origin/main` has no migrations (first merge), so it never blocks the very first migration set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: repo root, so the pathspecs below mean the same thing whether CI runs this from `backend/` or a human
#: runs it from the root — a relative `git ls-tree` from a subdirectory silently matches nothing, and a
#: guard that matches nothing is a guard that passes
ROOT = Path(__file__).resolve().parents[2]
PREFIX = "backend/migrations/versions/"
#: An edit to a merged revision is allowed only when the revision file itself carries this marker with a
#: reason, so the exception is always visible in the same diff that takes it (ADR-014). Without it the rule
#: stands exactly as ADR-001 wrote it.
APPROVED_MARKER = "# ALEMBIC-REWRITE-APPROVED:"


def _git(*args: str) -> str:
    result = subprocess.run(("git", "-C", str(ROOT), *args), capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def _approval_reason(path: str) -> str:
    """The marker text from the file as committed on HEAD ("" when absent, so an empty marker still fails)."""
    for line in _git("show", f"HEAD:{path}").splitlines():
        if APPROVED_MARKER in line:
            return line.split(APPROVED_MARKER, 1)[1].strip()
    return ""


def main() -> int:
    base = next((ref for ref in ("origin/main", "main") if _git("rev-parse", "--verify", ref.strip())), "")
    if not base:
        print("no base branch to compare against — skipping")
        return 0
    merged = {line.strip() for line in _git("ls-tree", "-r", "--name-only", base, "--", PREFIX).splitlines() if line.strip()}
    if not merged:
        print("base branch has no migrations yet — first migration set, nothing to protect")
        return 0
    changed = {line.split("\t")[-1].strip() for line in _git("diff", "--name-only", f"{base}...HEAD", "--", PREFIX).splitlines()}
    touched_status = {
        parts[0]: parts[1]
        for parts in (line.split("\t") for line in _git("diff", "--name-status", f"{base}...HEAD", "--", PREFIX).splitlines())
        if len(parts) == 2
    }
    touched_merged = sorted(
        {path for path, status in touched_status.items() if status in {"M", "D"} and path in merged} | (changed & merged)
    )
    edited, approved = [], {}
    for path in touched_merged:
        reason = _approval_reason(path)
        if path.endswith(".py") and reason:
            approved[path] = reason
        else:
            edited.append(path)
    if edited:
        print("ERROR: already-merged migration(s) touched:")
        for name in edited:
            print(f"  - {name}   (add a new revision instead of editing a merged one)")
        print(f"  an exception needs the marker {APPROVED_MARKER!r} + a reason inside the revision file")
        return 1
    for name, reason in approved.items():
        print(f"::warning file={name}::edited a merged revision under an approved exception: {reason}")
    added = sorted({path for path, status in touched_status.items() if status == "A"} - merged)
    print(f"migration immutability ok ({len(merged)} merged, {len(added)} new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
