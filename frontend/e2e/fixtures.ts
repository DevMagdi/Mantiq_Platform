import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** The API origin the panel and the Vite proxy both point at (see frontend/.env.example). */
export const API_ORIGIN = process.env.VITE_API_ORIGIN ?? "http://127.0.0.1:8000";

/** Demo credentials come from `python -m app.seed` — fictitious people only (spec rule 4). */
export const PASSWORD = "Br0kerHub!2026";
export const REPS = {
  sara: "sara.elsayed@brokerhub.test",
  nour: "nour.fahmy@brokerhub.test",
} as const;
export const MANAGER = "manager@brokerhub.test";
/** `users.manage` is the system-admin role's own — the users tab inside the settings hub needs it (ADR-016). */
export const ADMIN = "admin@brokerhub.test";
export const OWNER = "owner.mantiq@company.test";

export const LOGIN_BUTTON = { name: "تسجيل الدخول", exact: true } as const;

const EMAIL_FIELD = /البريد الإلكتروني/;
const PASSWORD_FIELD = /كلمة المرور/;


/** Log in through the real form, so the refresh cookie + profile fetch are exercised too. */
export async function login(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel(EMAIL_FIELD).fill(email);
  await page.getByLabel(PASSWORD_FIELD).fill(password);
  await page.getByRole("button", LOGIN_BUTTON).click();
  // the landing route depends on permission (ADR-014), so the helper waits for any of them
  await page.waitForURL(/\/(dashboard|portal|inbox|leads)/, { timeout: 20_000 });
}

/** API session for arranging data — goes through the same proxied /api the browser uses. */
export async function apiLogin(request: APIRequestContext, email: string, password = PASSWORD) {
  const response = await request.post("/api/v1/auth/login", { data: { email, password, remember_me: true } });
  expect(response.ok(), `login failed for ${email}`).toBeTruthy();
  const token = (await response.json()).access_token as string;
  return { authorization: `Bearer ${token}` };
}

/**
 * Which staff role can open a screen is decided by the API, and three specs once assumed the wrong
 * person: a sales rep has no `requests.view`, so `/inbox` is a 403 for them and the queue is empty by
 * design. `CS` is the role the inbox belongs to (18 of the seeded 24 requests, `manager` sees all 24).
 */
export const CS = "cs@brokerhub.test";

/** Open a request from the inbox queue (the first row that matches, or the first row at all). */
export async function openFirstRequest(page: Page) {
  await page.goto("/inbox");
  const row = page.locator("[data-request-row]").first();
  await expect(row).toBeVisible({ timeout: 20_000 });
  const requestId = await row.getAttribute("data-request-row");
  await row.click();
  // The queue is sorted by `last_activity_at`, and the page navigates itself to /inbox/<first row> a tick
  // after mount. Reading the id and then clicking `.first()` again could therefore write to one request and
  // assert on another — so the click is pinned to the row whose id the test is holding, and the URL is the
  // proof that the thread on screen is that request.
  await page.locator(`[data-request-row="${requestId}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/inbox/${requestId}$`), { timeout: 20_000 });
  return requestId;
}
