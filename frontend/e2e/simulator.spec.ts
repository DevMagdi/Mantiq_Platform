import { expect, test } from "@playwright/test";

import { ar } from "../src/lib/i18n/ar";

import { API_ORIGIN, apiLogin, login, MANAGER } from "./fixtures";

/**
 * Spec §8.1: the inbound channels must be demonstrable without third-party credentials. The simulator
 * shares the real webhook code path, so a green test here means the production path works too.
 */
test.describe("inbound simulator (spec §8.1)", () => {
  test("an inbound WhatsApp voice note opens a request whose player uses a signed URL", async ({ page, request }) => {
    test.skip(test.info().project.name !== "desktop", "the player lives in the inbox thread column — see the note at the top of e2e/inbox.spec.ts");
    const auth = await apiLogin(request, MANAGER);
    const mobile = "+201110002233";

    const sent = await request.post("/api/v1/dev/sim/whatsapp", {
      headers: auth,
      data: { mobile, sender_name: "منى — شركة ألف", media_kind: "audio/ogg", media_id: `e2e-${Date.now()}`, duration_s: 11 },
    });
    expect(sent.status(), await sent.text()).toBe(200);
    const requestId = (await sent.json()).request_id as string;
    expect(requestId).toBeTruthy();

    // an unrecognised number lands in the CS queue, so the manager is the account that always sees it
    await login(page, MANAGER);
    await page.goto(`/inbox/${requestId}`);

    // the thread's player is a custom control: the `<audio>` element carries the source and stays hidden
    // behind the «تشغيل» button (design sheet screen 2), so the promise to check is what the element is
    // wired to and what the user can press — not the visibility of a hidden node
    const audio = page.locator("audio").first();
    await expect(audio).toHaveCount(1);
    const src = await audio.getAttribute("src");
    expect(src, "the media element must be wired to the signed URL").toContain("/api/v1/media/");
    expect(src).toContain("sig=");
    expect(src).toContain("expires=");
    await expect(page.getByRole("button", { name: ar.inbox.play, exact: true }).first()).toBeVisible();

    // the same relative URL the browser hands to <audio> must answer 200 for staff
    const played = await request.get(src!);
    expect(played.status()).toBe(200);
    expect((await played.body()).byteLength).toBeGreaterThan(0);

    // what the player actually promises (InboxPage `toggle()`): a `play()` that cannot start — blocked
    // autoplay or an expired signature — must leave the control saying «تشغيل», never a playing state the
    // audio element does not have. There is no error caption in this app, so asserting one was invented.
    const play = page.getByRole("button", { name: ar.inbox.play, exact: true }).first();
    await play.click();
    await expect(play).toBeVisible();
    await expect(page.getByRole("button", { name: ar.inbox.pause, exact: true })).toHaveCount(0);
  });

  test("a replayed webhook does not duplicate the request", async ({ request }) => {
    const auth = await apiLogin(request, MANAGER);
    const externalId = `e2e-replay-${Date.now()}`;
    const payload = { mobile: "+201110004455", sender_name: "إعادة إرسال", body: "نفس الرسالة", external_id: externalId };

    const first = await request.post("/api/v1/dev/sim/whatsapp", { headers: auth, data: payload });
    expect(first.status()).toBe(200);
    const firstBody = await first.json();
    expect(firstBody.status).toBe("created");
    expect(firstBody.request_id).toBeTruthy();

    const replay = await request.post("/api/v1/dev/sim/whatsapp", { headers: auth, data: payload });
    expect(replay.status()).toBe(200);
    expect((await replay.json()).status).toBe("duplicate");

    // one request, one message — the provider retry must not double-post
    const detail = await request.get(`/api/v1/requests/${firstBody.request_id}`, { headers: auth });
    expect(detail.ok()).toBeTruthy();
    expect((await detail.json()).messages).toHaveLength(1);
  });

  test("the /dev/sim panel drives both inbound channels", async ({ page }) => {
    test.skip(test.info().project.name !== "desktop", "panel is a desktop utility");
    await page.goto(`${API_ORIGIN}/api/v1/dev/sim`);
    await expect(page.getByRole("heading", { name: /محاكي القنوات الواردة/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "إرسال واتساب وارد", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /مكالمة واردة مع recording/ })).toBeVisible();

    await page.getByRole("button", { name: "إرسال واتساب وارد", exact: true }).click();
    await expect(page.locator("#out")).toContainText("request_id", { timeout: 20_000 });
  });
});
