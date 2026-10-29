/**
 * Docs that point at code must point at code that exists (the plan's own rule, and the failure mode
 * `docs/ui-parity/screens.md` actually had: `features/leads/LeadsKanbanPage.tsx`, a file never written).
 *
 * The check is deliberately dumb — every path-shaped token in the two files that describe the
 * implementation must resolve to a file (or a directory, for section headings). That is cheap, and it
 * fails the moment a screen is renamed and the docs are left behind.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(import.meta.dirname, "../..");
const DOCS = ["docs/ui-parity/screens.md", "docs/STATUS.md", "docs/runbook.md"];

/** `features/x/Y.tsx`, `app/services/x.py`, `scripts/x.py`, `e2e/x.spec.ts`, `workers/tasks/x.py` … */
const FRONTEND_PATH = /(?:frontend\/)?((?:src\/)?features\/[\w./-]+\.(?:tsx|ts)|e2e\/[\w./-]+\.ts)/g;
const BACKEND_PATH = /(?<![\w/.])((?:backend\/)?app\/[\w./-]+\.py|scripts\/[\w./-]+\.py|migrations\/[\w./-]+\.py)/g;

function candidates(raw: string, root: string): string[] {
  const clean = raw.replace(/^\.\//, "").replace(/`/g, "");
  const list: string[] = [];
  if (clean.startsWith("features/") || clean.startsWith("src/")) list.push(path.join(root, "frontend", clean.startsWith("features/") ? `src/${clean}` : clean));
  if (clean.startsWith("e2e/")) list.push(path.join(root, "frontend", clean));
  if (clean.startsWith("app/") || clean.startsWith("scripts/") || clean.startsWith("migrations/")) list.push(path.join(root, "backend", clean));
  if (clean.startsWith("frontend/") || clean.startsWith("backend/")) list.push(path.join(root, clean));
  return [...new Set(list)];
}

const docs = DOCS.map((file) => ({ file, text: readFileSync(path.join(ROOT, file), "utf8") }));

describe("documentation paths resolve (spec: no stale docs)", () => {
  it.each(docs.map((d) => [d.file, d.text] as const))("%s names files that exist", (_file, text) => {
    const missing: string[] = [];
    for (const match of [...text.matchAll(FRONTEND_PATH), ...text.matchAll(BACKEND_PATH)]) {
      const raw = match[1];
      if (!raw) continue;
      const paths = candidates(raw, ROOT);
      if (paths.length === 0) continue;
      // a section heading may name a folder instead of a file
      const ok = paths.some((candidate) => existsSync(candidate) || existsSync(candidate.replace(/\.tsx?$/, "")));
      if (!ok) missing.push(`${raw} → ${paths.map((p) => path.relative(ROOT, p)).join(", ")}`);
    }
    expect(missing, `stale doc paths:\n${missing.join("\n")}`).toEqual([]);
  });
});
