/// <reference types="node" />
/**
 * Token parity gate (spec §4): the CSS custom properties must equal the design sheet, character for
 * character. This exists because "we used the tokens" is not verifiable by eye in a screenshot.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(import.meta.dirname, "..");
const css = readFileSync(path.join(ROOT, "src/styles/tokens.css"), "utf8");

const declared = new Map<string, string>();
for (const match of css.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) {
  declared.set(match[1] as string, (match[2] as string).trim().toLowerCase());
}

const SHEET = {
  "--bg": "#f3f4f9",
  "--primary": "#4f46e5",
  "--primary-soft": "#eef0ff",
  "--sidebar": "#0e1424",
  "--kanban-col": "#e9ecf6",
  "--row-warn": "#fffaeb",
  "--row-ok": "#f4fbf7",
  "--g-ink": "#05603a",
  "--g-bg": "#f0fdf4",
  "--a-ink": "#92400e",
  "--a-bg": "#fffaeb",
  "--r-ink": "#b42318",
  "--r-bg": "#fef3f2",
  "--b-ink": "#175cd3",
  "--b-bg": "#eff6ff",
  "--t-ink": "#0f766e",
  "--t-bg": "#ecfdf5",
} as const;

describe("design tokens (spec §4)", () => {
  for (const [name, value] of Object.entries(SHEET)) {
    it(`${name} = ${value}`, () => {
      expect(declared.get(name)).toBe(value);
    });
  }

  it("the base soft tone is exactly one — no second indigo (ADR-016 §5)", () => {
    const soft = [...declared.keys()].filter((name) => name.includes("soft"));
    expect(soft).toEqual(["--primary-soft"]);
    expect(declared.get("--primary-soft")).toBe("#eef0ff");
    // the value the deleted `--soft-bg` carried must not have been smuggled in under another name
    expect([...declared.values()]).not.toContain("#eef2ff");
  });

  it("card, button and badge geometry come from the sheet", () => {
    // read as text on purpose: the config is plain JS and this suite should not depend on its shape
    const tailwind = readFileSync(path.join(ROOT, "tailwind.config.js"), "utf8");
    expect(tailwind).toMatch(/card:\s*"18px"/);
    expect(tailwind).toMatch(/btn:\s*"12px"/);
    expect(tailwind).toMatch(/pill:\s*"999px"/);
    expect(tailwind).toMatch(/transitionDuration:\s*\{\s*DEFAULT:\s*"150ms"/);
    expect(tailwind).toMatch(/sans:\s*\['"IBM Plex Sans Arabic"'/);
  });

  it("the card padding and search width the sheet repeats are wired, not incidental", () => {
    const index = readFileSync(path.join(ROOT, "src/styles/index.css"), "utf8");
    expect(index).toMatch(/\.card\s*\{[^}]*padding:\s*18px 20px/s);
    const ui = readFileSync(path.join(ROOT, "src/components/ui.tsx"), "utf8");
    expect(ui).toMatch(/width\d*\s*=\s*260/); // the search field default
    expect(ui).toMatch(/size\w*\s*=\s*38/); // the avatar default
  });

  it("every badge tone resolves to a sheet pair", () => {
    const ui = readFileSync(path.join(ROOT, "src/components/ui.tsx"), "utf8");
    for (const tone of ["g", "a", "r", "b", "t"] as const) {
      expect(ui, `Badge must use var(--${tone}-bg) / var(--${tone}-ink)`).toContain(`var(--${tone}-bg)`);
      expect(ui).toContain(`var(--${tone}-ink)`);
    }
  });
});
