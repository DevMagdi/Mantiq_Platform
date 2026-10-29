import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { ar } from "@/lib/i18n/ar";
import { en } from "@/lib/i18n/en";

function keys(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, nested]) =>
    nested && typeof nested === "object" && !Array.isArray(nested)
      ? keys(nested as Record<string, unknown>, `${prefix}${key}.`)
      : [`${prefix}${key}`],
  );
}

function lookup(bag: unknown, key: string): string | undefined {
  const [head, ...rest] = key.split(".");
  let cursor: unknown = (bag as Record<string, unknown>)[head ?? ""];
  for (const part of rest) cursor = (cursor as Record<string, unknown>)?.[part ?? ""];
  return typeof cursor === "string" ? cursor : undefined;
}

/** Arabic is the source of truth; a missing English key is a bug, not a fallback (spec §11). */
describe("translation parity", () => {
  const arabic = keys(ar as unknown as Record<string, unknown>);
  const english = keys(en as unknown as Record<string, unknown>);

  it("has at least the screens' worth of keys", () => {
    expect(arabic.length).toBeGreaterThan(180);
  });

  it("mirrors every Arabic key in English", () => {
    expect(english.filter((key) => !arabic.includes(key))).toEqual([]);
    expect(arabic.filter((key) => !english.includes(key))).toEqual([]);
  });

  it("ships a string for every key the settings hub asks for", () => {
    // i18next answers a missing key with the key itself, which would print `settings.testOk` on screen —
    // a bug the parity test above cannot see, because both languages can be missing it together.
    const dir = path.join(__dirname, "../src/features/settings");
    const used = new Set<string>();
    for (const file of readdirSync(dir)) {
      if (!file.endsWith(".tsx") && !file.endsWith(".ts")) continue;
      const source = readFileSync(path.join(dir, file), "utf8");
      for (const match of source.matchAll(/\bt\(\s*"([a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)"/g)) used.add(match[1] as string);
    }
    expect(used.size).toBeGreaterThan(20);
    const missing = [...used].filter((key) => !lookup(ar, key) || !lookup(en, key));
    expect(missing, "keys the UI references but no dictionary defines").toEqual([]);
  });

  it("labels every provider, badge tone and payment plan the screen can show", () => {
    // these three are looked up by template literal (`settings.provider.${p}`), so the scan above misses them
    const bags = { ar, en };
    for (const language of Object.keys(bags)) {
      const bag = (bags as Record<string, Record<string, unknown>>)[language] as Record<string, any>;
      for (const provider of ["whatsapp", "telephony", "email"]) {
        expect(bag.settings.provider[provider], `${language}.${provider}`).toBeTruthy();
        expect(bag.settings.provider[`${provider}Note`], `${language}.${provider}Note`).toBeTruthy();
      }
      // R9 §1.D: the badges are Live / جاهز / غير مُعد / فشل آخر اختبار — `sim` is no longer a state
      for (const status of ["live", "ready", "unset", "error"]) {
        expect(bag.settings.status[status], `${language}.${status}`).toBeTruthy();
      }
      for (const plan of Object.keys(bag.settings.plan)) expect(bag.settings.plan[plan]).toBeTruthy();
    }
    // the plans are a closed list owned by the model; a plan without a label would render its raw value
    expect(Object.keys(ar.settings.plan).sort()).toEqual(Object.keys(en.settings.plan).sort());
  });

  it("never leaves an empty string behind", () => {
    const empties = [...arabic, ...english].filter((key) => {
      const [namespace, ...rest] = key.split(".");
      const bag = (en as Record<string, unknown>)[namespace ?? ""] ?? (ar as Record<string, unknown>)[namespace ?? ""];
      let cursor: unknown = bag;
      for (const part of rest) cursor = (cursor as Record<string, unknown>)?.[part ?? ""];
      return typeof cursor === "string" && cursor.trim().length === 0;
    });
    expect(empties).toEqual([]);
  });
});
