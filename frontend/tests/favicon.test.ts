/** R13 §3 (ADR-034): the built app carries the Mantiq icon set — never a default or a previous brand. */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "..");
const html = readFileSync(resolve(root, "index.html"), "utf8");
const manifest = JSON.parse(readFileSync(resolve(root, "public/manifest.webmanifest"), "utf8")) as {
  name: string;
  short_name: string;
  theme_color: string;
  background_color: string;
  icons: { src: string; purpose?: string }[];
};

describe("favicon set", () => {
  it("index.html references the ICO, PNG sizes, apple-touch and the manifest", () => {
    expect(html).toContain('href="/favicon.ico"');
    expect(html).toContain('href="/favicon-32.png"');
    expect(html).toContain('href="/apple-touch-icon.png"');
    expect(html).toContain('href="/manifest.webmanifest"');
    expect(html).not.toContain("vite.svg");
  });

  it("every referenced icon exists in public/", () => {
    for (const name of [
      "favicon.ico",
      "favicon-32.png",
      "apple-touch-icon.png",
      "icon-192.png",
      "icon-512.png",
      "icon-512-maskable.png",
      "og-image.png",
      "manifest.webmanifest",
      "brand/mantiq-mark.svg",
      "brand/mantiq-mark-on-navy.svg",
    ]) {
      expect(existsSync(resolve(root, "public", name)), name).toBe(true);
    }
    expect(manifest.icons.map((i) => i.src)).toEqual(["/icon-192.png", "/icon-512.png", "/icon-512-maskable.png"]);
  });

  it("the tab title and manifest name the product Mantiq / منطق", () => {
    expect(html).toContain("<title>منطق");
    expect(html).not.toMatch(/Broker Hub/i);
    expect(manifest.name).toBe("Mantiq · منطق");
    expect(manifest.short_name).toBe("Mantiq");
  });

  it("theme colour is Deep Navy, the chrome colour — not the old indigo", () => {
    expect(html).toContain('name="theme-color" content="#0E1728"');
    expect(manifest.theme_color).toBe("#0E1728");
    expect(manifest.background_color).toBe("#FAFBFC");
    expect(html.toLowerCase()).not.toContain("#4f46e5");
  });

  it("the iOS/Android icons are real images of the expected size", () => {
    // PNG IHDR: bytes 16..24 are width/height, big-endian
    for (const [name, size] of [
      ["favicon-32.png", 32],
      ["apple-touch-icon.png", 180],
      ["icon-192.png", 192],
      ["icon-512.png", 512],
      ["icon-512-maskable.png", 512],
    ] as const) {
      const buffer = readFileSync(resolve(root, "public", name));
      expect(buffer.subarray(1, 4).toString(), name).toBe("PNG");
      expect(buffer.readUInt32BE(16), `${name} width`).toBe(size);
      expect(buffer.readUInt32BE(20), `${name} height`).toBe(size);
    }
    const ico = readFileSync(resolve(root, "public/favicon.ico"));
    expect(ico.readUInt16LE(0)).toBe(0); // reserved
    expect(ico.readUInt16LE(2)).toBe(1); // type: icon
    expect(ico.readUInt16LE(4)).toBeGreaterThanOrEqual(3); // 16/32/48 frames
  });
});
