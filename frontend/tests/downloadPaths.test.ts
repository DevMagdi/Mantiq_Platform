/**
 * `downloadUrls` come in two shapes and both must reach the same route.
 *
 * The portal passes the API's own `document.download_url` (`/api/v1/portal/policies/<id>/document`), while the
 * policies table builds `/policies/<id>/document` itself. Prefixing the first with `/api/v1` produced
 * `/api/v1/api/v1/...`, which is a 404 — and a 404 behind a download button looks exactly like a button that
 * does nothing. The e2e suite found it as a timeout, not as a wrong file.
 */
import { describe, expect, it } from "vitest";

import { downloadUrl } from "@/lib/api/download";

describe("downloadUrl", () => {
  it("leaves a screen-built path alone", () => {
    expect(downloadUrl("/policies/abc/document")).toBe("/api/v1/policies/abc/document");
    expect(downloadUrl("/exports/insurer-performance")).toBe("/api/v1/exports/insurer-performance");
  });

  it("does not prefix an API-supplied path twice", () => {
    expect(downloadUrl("/api/v1/portal/policies/abc/document")).toBe("/api/v1/portal/policies/abc/document");
    expect(downloadUrl("/api/v1/portal/attachments/xyz")).toBe("/api/v1/portal/attachments/xyz");
  });

  it("keeps an absolute URL as it arrived", () => {
    expect(downloadUrl("https://storage.test/signed/policy.pdf?sig=1")).toBe("https://storage.test/signed/policy.pdf?sig=1");
  });
});
