/// <reference types="node" />
/**
 * Brand + token gate (ADR-034). The previous version of this file pinned the old design-sheet palette
 * (#4f46e5 indigo, #f3f4f9 page, #0e1424 rail…). R13 replaced that palette wholesale, so the gate now
 * pins the *Mantiq* palette and, more importantly, refuses the old one: “we used the tokens” is not
 * verifiable by eye in a screenshot, and neither is “the old brand is gone”.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = path.resolve(import.meta.dirname, "..");
const css = readFileSync(path.join(ROOT, "src/styles/tokens.css"), "utf8");

const declared = new Map<string, string>();
for (const match of css.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) {
  declared.set(match[1] as string, (match[2] as string).trim());
}

/** The canonical palette — the brief's table, character for character. */
const BRAND = {
  "--color-brand": "#16273b",
  "--color-brand-deep": "#0e1728",
  "--color-network": "#243553",
  "--color-gold": "#bda77e",
  "--color-gold-light": "#e3d2a8",
  "--color-gold-dark": "#8f7a52",
  "--color-on-dark": "#e8e2d4",
} as const;

/** Semantic tokens the brief requires by name. */
const REQUIRED = [
  "--color-bg",
  "--color-surface",
  "--color-ink",
  "--color-muted",
  "--color-border",
  "--color-sidebar",
  "--color-sidebar-text",
  "--color-sidebar-active",
  "--color-sidebar-hover",
  "--color-accent",
  "--color-accent-hover",
  "--color-accent-muted",
  "--color-on-accent",
  "--gradient-mark",
] as const;

/** Every hex the old identity was built on — none of them may survive as a value in the token file. */
const RETIRED = ["#4f46e5", "#4338ca", "#3730a3", "#eef0ff", "#0e1424", "#1c2547", "#aeb7d0", "#f3f4f9", "#edeff6", "#e9ecf6", "#c7d2fe", "#2dd4bf"];

function sourceFiles(dir = path.join(ROOT, "src")): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(tsx?|css)$/.test(entry)) out.push(full);
  }
  return out;
}

/** Normalise an alias chain: `--bg: var(--color-bg)` → `--bg` resolves to `--bg`'s eventual value. */
function resolve(name: string, seen = new Set<string>()): string | undefined {
  const raw = declared.get(name);
  if (!raw || seen.has(name)) return undefined;
  const alias = /^var\((--[a-z0-9-]+)\)$/.exec(raw);
  if (alias) return resolve(alias[1] as string, new Set([...seen, name]));
  return raw;
}

describe("Mantiq brand tokens (ADR-034)", () => {
  it.each(Object.entries(BRAND))("%s is the canonical %s", (name, value) => {
    expect(declared.get(name)?.toLowerCase()).toBe(value);
  });

  it("declares every semantic token the brief requires", () => {
    const missing = REQUIRED.filter((name) => !declared.has(name));
    expect(missing).toEqual([]);
  });

  it("brand colours flow only through variables — the hex appears once, in the palette block", () => {
    for (const [name, value] of Object.entries(BRAND)) {
      const hits = [...declared.entries()].filter(([, raw]) => raw.toLowerCase() === value);
      expect(hits.map(([key]) => key), `${value} is defined more than once`).toEqual([name]);
    }
    // legacy names resolve to the same colour as the semantic token, never to a hex of their own
    expect(declared.get("--primary")).toBe("var(--color-accent)");
    expect(declared.get("--sidebar")).toBe("var(--color-sidebar)");
    expect(declared.get("--bg")).toBe("var(--color-bg)");
    expect(declared.get("--card")).toBe("var(--color-surface)");
    expect(declared.get("--ink")).toBe("var(--color-ink)");
    expect(declared.get("--line")).toBe("var(--color-border)");
    expect(declared.get("--teal")).toBe("var(--color-accent)");
    expect(resolve("--primary")).toBe("#bda77e");
    expect(resolve("--sidebar")).toBe("#0e1728");
  });

  it("the sidebar active indicator is gold and on-accent text is navy (never white on gold)", () => {
    expect(resolve("--color-sidebar-active")).toBe("#bda77e"); // Mantiq Gold
    expect(resolve("--color-sidebar-hover")).toBe("#243553"); // Network Blue
    expect(resolve("--color-sidebar")).toBe("#0e1728"); // Deep Navy
    expect(resolve("--color-on-accent")).toBe("#0e1728"); // Deep Navy label on gold
    expect(resolve("--color-sidebar-text")).toBe("#e8e2d4"); // Text on dark
    expect(resolve("--color-accent-hover")).toBe("#8f7a52"); // Gold Dark
  });

  it("the mark gradient is the three-stop gold diagonal", () => {
    const gradient = declared.get("--gradient-mark") ?? "";
    expect(gradient).toContain("linear-gradient(135deg");
    expect(gradient).toContain("var(--color-gold-light)");
    expect(gradient).toContain("var(--color-gold)");
    expect(gradient).toContain("var(--color-gold-dark)");
  });

  it("the light UI is white/near-white, not beige and not the old #f3f4f9", () => {
    expect(declared.get("--color-bg")?.toLowerCase()).toBe("#fafbfc");
    expect(declared.get("--color-surface")?.toLowerCase()).toBe("#ffffff");
  });

  it("no retired brand hex survives anywhere in src/", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const text = readFileSync(file, "utf8").toLowerCase();
      for (const hex of RETIRED) {
        if (text.includes(hex)) offenders.push(`${path.relative(ROOT, file)} → ${hex}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("no raw hex is used as a colour outside the token file", () => {
    // `#fff`/`#000` would be a second source of truth too — components use var(--color-*) or Tailwind
    // names that map to them. Data, not colours: the mark's SVG fallbacks live in the token-adjacent
    // BrandMark component and are asserted separately below.
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      if (file.endsWith("tokens.css") || file.endsWith("BrandMark.tsx")) continue;
      const text = readFileSync(file, "utf8");
      for (const match of text.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) offenders.push(`${path.relative(ROOT, file)} → ${match[0]}`);
    }
    expect(offenders).toEqual([]);
  });

  it("card, button and badge geometry come from the sheet", () => {
    const tailwind = readFileSync(path.join(ROOT, "tailwind.config.js"), "utf8");
    expect(tailwind).toMatch(/card:\s*"18px"/);
    expect(tailwind).toMatch(/btn:\s*"12px"/);
    expect(tailwind).toMatch(/pill:\s*"999px"/);
    expect(tailwind).toMatch(/transitionDuration:\s*\{\s*DEFAULT:\s*"150ms"/);
    expect(tailwind).toMatch(/sans:\s*\['"IBM Plex Sans Arabic"'/);
    // the Tailwind map is a map: every brand colour it names must point at a CSS variable
    const hexInTailwind = [...tailwind.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0]);
    expect(hexInTailwind, "tailwind.config.js must not hold hex values").toEqual([]);
  });

  it("every badge tone resolves to a token pair", () => {
    const ui = readFileSync(path.join(ROOT, "src/components/ui.tsx"), "utf8");
    for (const tone of ["g", "a", "r", "b", "t"] as const) {
      expect(ui, `Badge must use var(--${tone}-bg) / var(--${tone}-ink)`).toContain(`var(--${tone}-bg)`);
      expect(ui).toContain(`var(--${tone}-ink)`);
    }
  });

  it("the card padding and search width the sheet repeats are wired, not incidental", () => {
    const index = readFileSync(path.join(ROOT, "src/styles/index.css"), "utf8");
    expect(index).toMatch(/\.card\s*\{[^}]*padding:\s*18px 20px/s);
    const ui = readFileSync(path.join(ROOT, "src/components/ui.tsx"), "utf8");
    expect(ui).toMatch(/width\d*\s*=\s*260/); // the search field default
    expect(ui).toMatch(/size\w*\s*=\s*38/); // the avatar default
  });
});

describe("Mantiq mark", () => {
  it("the component, the exported SVG and the icon generator share one geometry", () => {
    const component = readFileSync(path.join(ROOT, "src/components/BrandMark.tsx"), "utf8");
    const generator = readFileSync(path.join(ROOT, "scripts/generate-brand-icons.py"), "utf8");
    const svg = readFileSync(path.join(ROOT, "public/brand/mantiq-mark.svg"), "utf8");

    const pathData = /MARK_PATH = "([^"]+)"/.exec(component)?.[1];
    expect(pathData).toBeTruthy();
    expect(svg).toContain(`d="${pathData}"`);
    // the python generator builds the path from points; the points are the ones in the component
    expect(generator).toContain("PATH_POINTS = [(14.0, 18.5), (32.0, 39.0), (50.0, 18.5), (50.0, 49.0)]");
    expect(component).toContain("{ cx: 14, cy: 18.5");
    expect(generator).toMatch(/NODES = \[\s*\(14\.0, 18\.5/);
    expect(generator).toContain('"#E3D2A8"');
    expect(generator).toContain('"#BDA77E"');
    expect(generator).toContain('"#8F7A52"');
  });

  it("the mark gradient stops read the token variables, with the canonical hex only as fallback", () => {
    const component = readFileSync(path.join(ROOT, "src/components/BrandMark.tsx"), "utf8");
    expect(component).toContain("var(--color-gold-light, #E3D2A8)");
    expect(component).toContain("var(--color-gold, #BDA77E)");
    expect(component).toContain("var(--color-gold-dark, #8F7A52)");
  });
});
