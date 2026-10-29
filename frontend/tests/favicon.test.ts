/** R12 §E: the built app must carry Broker Hub's own icon set — never the Vite default. */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "..");
const html = readFileSync(resolve(root, "index.html"), "utf8");

describe("favicon set", () => {
  it("index.html references the ICO, PNG sizes, apple-touch and the manifest", () => {
    expect(html).toContain('href="/favicon.ico"');
    expect(html).toContain('href="/favicon-32.png"');
    expect(html).toContain('href="/apple-touch-icon.png"');
    expect(html).toContain('href="/manifest.webmanifest"');
    expect(html).not.toContain("vite.svg");
  });

  it("every referenced icon exists in public/", () => {
    for (const name of ["favicon.ico", "favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "manifest.webmanifest"]) {
      expect(existsSync(resolve(root, "public", name)), name).toBe(true);
    }
    const manifest = JSON.parse(readFileSync(resolve(root, "public/manifest.webmanifest"), "utf8")) as { icons: { src: string }[] };
    expect(manifest.icons.map((i) => i.src)).toEqual(["/icon-192.png", "/icon-512.png"]);
  });
});
