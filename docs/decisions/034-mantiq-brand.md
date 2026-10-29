# ADR-034 — Mantiq brand system: content white · accent gold · chrome dark navy

## Status
Accepted — R13 (2026-10-29). Supersedes the palette section of the UI design sheet as the *source of
truth for colour*; the sheet's geometry (18px cards, 12px buttons, 999px pills), typography (IBM Plex
Sans Arabic, 14/22 base) and layout rules are untouched. Where this ADR and the sheet disagree, this ADR
wins **for visual identity only** — no business rule, route, permission or schema changed.

Product rename in the same round: **Broker Hub / وسيط → Mantiq / منطق**, company **Mantiq Technologies**.
Internal identifiers that are not user-visible stay as they are (see §7).

## Decisions

### 1. One palette, one map, one file
`frontend/src/styles/tokens.css` is the only place a brand colour is written as a hex. Every other
surface — Tailwind classes, inline `style`, the print letterhead, the icon generator — reads either a
`var(--color-*)` or a Tailwind name that maps to one. Legacy names (`--bg`, `--card`, `--primary`,
`--sidebar`, `--line`, `--teal`, `--g-bg`…) are **aliases** to the semantic tokens; they carry no value
of their own, so there is no second hex to drift out of sync.

Guard: `frontend/tests/tokens.test.ts` parses the stylesheet and fails if (a) a canonical hex is defined
twice, (b) a required semantic token is missing, (c) an alias holds a literal hex, (d) **any** retired
brand hex (`#4f46e5`, `#4338ca`, `#eef0ff`, `#f3f4f9`, `#0e1424`, `#1c2547`, `#aeb7d0`, `#2dd4bf`,
`#c7d2fe`, `#e9ecf6`, `#edeff6`) appears anywhere under `frontend/src`, or (e) a raw hex is used as a
colour outside the token file (the mark's SVG fallbacks excepted and asserted separately).

### 2. Palette (canonical — do not invent extra brand colours)

| Token | Hex | RGB | Role |
|---|---|---|---|
| Mantiq Navy `--color-brand` | `#16273B` | 22, 39, 59 | Primary brand on light: ink, headers, brand text |
| Deep Navy `--color-brand-deep` | `#0E1728` | 14, 23, 40 | Sidebar / darkest chrome; text on gold |
| Network Blue `--color-network` | `#243553` | 36, 53, 83 | Sidebar hover, elevated dark surface |
| Mantiq Gold `--color-gold` | `#BDA77E` | 189, 167, 126 | Accent: primary buttons, active nav, focus source, chart series 1 |
| Gold Light `--color-gold-light` | `#E3D2A8` | 227, 210, 168 | Gradient start, soft gold fill source (≤12% alpha on light) |
| Gold Dark `--color-gold-dark` | `#8F7A52` | 143, 122, 82 | Gradient end, hover/pressed, focus ring |
| Text on dark `--color-on-dark` | `#E8E2D4` | 232, 226, 212 | Text & icons on navy |

Derived (documented, not extra brand colours):

| Token | Value | Why |
|---|---|---|
| `--color-accent-ink` | `#6E5C3B` | Gold is a **fill** (2.3:1 on white), never body text. Mantiq Navy mixed toward gold gives a 6.1:1 link/number colour that still reads as "the gold family". |
| `--color-accent-muted` | `rgba(227,210,168,.12)` | Gold Light at the 12% ceiling — selected rows, soft chips. |
| `--color-focus` | = Gold Dark | The focus ring must clear 3:1 against white; Mantiq Gold does not (1.9:1). Gold Dark is 4.1:1. |
| `--color-muted` | `#687380` | Mantiq Navy at 65% over white — 4.8:1 on `--color-bg`, AA for normal text. |

**Gold mark gradient** (`--gradient-mark`, mark only): 135° `#E3D2A8 → #BDA77E → #8F7A52`.

### 3. Rules of the house

* **Content white.** Page `#FAFBFC`, surfaces `#FFFFFF`, borders `rgba(22,39,59,.10)`. No beige, no
  grey-lilac page. Body text is Deep Navy / Mantiq Navy — never `#000`.
* **Accent gold.** Primary buttons, active tabs, selected chips, focus-visible, chart series 1.
  Hover/pressed → Gold Dark. Soft gold backgrounds only at Gold Light ≤ 12%.
* **Label on gold is Deep Navy** (`--color-on-accent`), not white. White on `#BDA77E` is 1.9:1 and fails
  AA; Deep Navy on gold is 7.9:1. This is the one place the brief and the old sheet clearly conflicted
  (the sheet's primary button was white-on-indigo), and the brief wins.
* **Chrome dark navy.** Sidebar `#0E1728`, default label `#E8E2D4` at 100% (it is the *sidebar's* body
  text, so AA needs the full value), section labels at 62%, hover `#243553`, active = soft gold fill
  (`rgba(189,167,126,.14)`) + gold label + a 3px gold bar on the **inline-start** edge.
* **Semantic status** stays functional, recalibrated on white + navy, no neon:
  `success #1B5E3A on #EEF6F0` · `warn #8A5A12 on #FDF6EA` · `danger #A32A22 on #FDF0EF` ·
  `info #1F4E8C on #EDF2F9` · `neutral #17565F on #ECF3F4`. Every pair is ≥ 5.3:1.
* **Motion** unchanged (150ms transitions, the existing reduced-motion override).
* **RTL** unchanged and now asserted for the active indicator: the bar is positioned with
  `insetInlineStart`, so it is on the right in Arabic and the left in English.

### 4. Charts & data viz
Series order is fixed by token, not by screen: **series 1 = Mantiq Gold** (`--color-chart-1`),
**series 2 = Network Blue** (`--color-chart-2`), **series 3 = Gold Dark** (`--color-chart-3`).
Gridlines are navy at 8% (`--color-chart-grid`), axes take `--color-chart-axis`; no `#000` axis.
The dashboard's channel breakdown and the reports bar chart both read these three tokens plus the
neutral track, so two screens cannot pick two different "first series".

### 5. Logo, favicon, PWA
The mark is an abstract «M» whose vertices are network nodes, in the gold diagonal gradient — minimal,
one geometry, and it survives 16px (the node dots are dropped below 20px on purpose).

* `src/components/BrandMark.tsx` — `MantiqMark` (inline SVG, gradient stops are CSS variables) and
  `MantiqLockup` (mark + wordmark + optional legal line). Used by the sidebar, the login panel and the
  portal banner. The wordmark string is always `t("common.appName")` — «منطق» / “Mantiq” — so it can
  never be a stale hard-coded name.
* `frontend/public/brand/mantiq-mark.svg`, `mantiq-mark-on-navy.svg` — the exported artwork.
* `frontend/public/{favicon.ico, favicon-32.png, apple-touch-icon.png, icon-192.png, icon-512.png,
  icon-512-maskable.png, og-image.png}` — generated **from the same numbers** by
  `frontend/scripts/generate-brand-icons.py` (Pillow, offline, deterministic).
* `index.html` and `manifest.webmanifest`: `theme-color` **#0E1728**, `background_color` **#FAFBFC**,
  title «منطق · منصة إدارة الوساطة التأمينية», manifest name “Mantiq · منطق”.
* `tests/favicon.test.ts` checks the wiring, the manifest, and the actual PNG dimensions; `tokens.test.ts`
  checks the component, the SVG and the generator share one path/nodes/gradient.
* Tab title follows the locale: `applyDirection()` writes `document.title` from
  `common.documentTitle` after every language change.

### 6. Copy (brand strings only)
`common.appName`, `common.legalName` (Mantiq Technologies), `common.documentTitle`, `portal.brand`,
the portal-credentials hand-off message, `index.html`, the manifest, the API title/description, the
e-mail `from_name` default, the simulator panel title, the XLSX/PDF letterheads, and the demo seed
`owner.mantiq@company.test` (already Mantiq).

The demo **credentials** were renamed too, because they are printed on the sign-in screen and a
"Broker Hub" string there is old brand in UI chrome, not infrastructure: `@brokerhub.test` →
`@mantiq.test`, the seed password `Br0kerHub!2026` → `Mantiq!2026` (still overridable with the
`SEED_PASSWORD` env var), the sender default `no-reply@brokerhub.local` → `no-reply@mantiq.local`,
and the synthetic portal address `@portal.brokerhub.local` → `@portal.mantiq.local`. The rename was
applied in lockstep across the seed, the login screen, `e2e/`, the backend test suite, the load
scripts and CI, so no fixture logs in with a credential the seed no longer creates. A mixed-script
typo on the same screen ("سara") was corrected to "سارة".

**Domain copy is untouched** — no lead, SLA, finance, policy or compliance wording was rewritten, and
the README may say “Mantiq (Brokerage OS)” once.

### 7. Deliberately *not* renamed
`@broker-hub/frontend` package name, the `brokerhub` Postgres database and the `brokerhub_app` /
`brokerhub_load` / `brokerhub_deploy` roles, Docker service names, the S3 bucket, the `bh.lang`
localStorage key, JWT issuer, secret keys and environment-variable **names**. None of them is
user-facing product identity; each is a migration with its own risk, and the brief forbids scope
creep for brand. They are listed here so a future reader does not mistake them for leftovers.
(Demo credentials used to be on this list; see §6 — they moved because they are rendered on screen.
Dated records under `docs/perf/`, `docs/qa/` and ADR-001/014/022 keep the old strings as history.)

### 8. Conflicting prior ADR / sheet rules, and how they were resolved
* **The design sheet §4 palette** (indigo `#4F46E5`, page `#F3F4F9`, rail `#0E1424`, badge pairs) is
  retired as identity. Its *non-colour* numbers (geometry, type scale, spacing) still hold, and
  `tokens.test.ts` still pins them.
* **White-on-primary** (sheet) vs **navy-on-gold** (brief): the brief wins, because it is the only pair
  that passes AA. Recorded here as an intentional visual deviation.
* **R12's favicon set** (ADR-032/R12 §E) is replaced: same filenames, new artwork, same test enforcing
  that they exist and that the manifest lists them.

## Consequences
* A palette change is one file edit; the grep for the old identity is a test, not a habit.
* Gold is an accent: any future screen that uses gold for long text will fail the contrast rule, not the
  test suite — reviewers should hold that line.
* The icon generator needs Pillow to *regenerate*; the committed PNGs mean the build and CI never do.

## Verification
`frontend`: `npm run build` · `npm run lint` · `npm run typecheck` · `npx vitest run` (20 files, 135
tests) · `E2E_USE_PREVIEW=1 npx playwright test --grep-invert "R6 evidence"` on a freshly seeded
database (151 passed · 0 failed · 51 skipped). `backend`: `pytest` (317 passed · 9 skipped) ·
`ruff` · `mypy` · `python scripts/check_openapi_fresh.py`. The literal outputs are recorded in
`docs/STATUS.md` (R13).

Two test-infrastructure defects surfaced while closing this round and were fixed rather than
explained away: `e2e/global-setup.ts` launched a browser that ignored `E2E_CHROMIUM_PATH` (so the
warm-up pass could not run anywhere Playwright's own Chromium is unavailable) and warmed only
`/login`, while Vite dev compiles route chunks on first visit. It now honours the configured
executable, signs in as the seeded admin and visits each screen once.
`scripts/capture-brand-screens.mjs` was also still signing in with the pre-rename demo credentials.

Honest limit of that fix: on this sandbox's dev server `settings.spec.ts` still timed out on a
*different* test in 3 of 4 isolated runs (8–20s expectations, no 4xx/5xx), so the flake is
environmental timing, not a page defect. The suite is therefore judged on the built preview — the
configuration CI uses (`E2E_USE_PREVIEW=1`) — where the same specs are 100% green.
