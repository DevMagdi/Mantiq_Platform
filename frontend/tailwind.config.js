/**
 * Mantiq design tokens (ADR-034). Every colour is a CSS variable from `src/styles/tokens.css` — this
 * file maps names, it never holds a hex. The semantic names are the ones new code should use
 * (`surface`, `accent`, `on-accent`, `ink`, …); the legacy names (`card`, `bg`, `primary`, …) stay
 * mapped to the same variables so the 60 existing screens keep working without a fork in the palette.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── semantic ─────────────────────────────────────────────────────────────────────────────
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        ink: { DEFAULT: "var(--color-ink)", strong: "var(--color-ink-strong)" },
        muted: "var(--color-muted)",
        border: { DEFAULT: "var(--color-border)", strong: "var(--color-border-strong)" },
        track: "var(--color-track)",
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          muted: "var(--color-accent-muted)",
          ink: "var(--color-accent-ink)",
        },
        "on-accent": "var(--color-on-accent)",
        "on-dark": "var(--color-on-dark)",
        focus: "var(--color-focus)",
        chart: {
          1: "var(--color-chart-1)",
          2: "var(--color-chart-2)",
          3: "var(--color-chart-3)",
          grid: "var(--color-chart-grid)",
        },
        sidebar: {
          DEFAULT: "var(--color-sidebar)",
          text: "var(--color-sidebar-text)",
          active: "var(--color-sidebar-active)", // gold label / gold indicator
          "active-bg": "var(--color-sidebar-active-bg)", // soft gold fill behind it
          hover: "var(--color-sidebar-hover)",
          muted: "var(--color-sidebar-muted)",
          line: "var(--color-sidebar-line)",
        },
        // ── legacy aliases (same variables, never a second value) ───────────────────────────────
        card: "var(--card)",
        line: { DEFAULT: "var(--line)", 2: "var(--line-2)" },
        "line-2": "var(--line-2)",
        "sidebar-text": "var(--sidebar-text)",
        primary: {
          DEFAULT: "var(--primary)",
          hover: "var(--primary-hover)",
          soft: "var(--primary-soft)",
          ink: "var(--primary-ink)",
        },
        teal: "var(--teal)",
        kanban: "var(--kanban-col)",
        "row-warn": "var(--row-warn)",
        "row-ok": "var(--row-ok)",
        g: { bg: "var(--g-bg)", ink: "var(--g-ink)" },
        a: { bg: "var(--a-bg)", ink: "var(--a-ink)" },
        r: { bg: "var(--r-bg)", ink: "var(--r-ink)" },
        b: { bg: "var(--b-bg)", ink: "var(--b-ink)" },
        t: { bg: "var(--t-bg)", ink: "var(--t-ink)" },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans Arabic"', '"IBM Plex Sans"', "system-ui", "sans-serif"],
      },
      borderRadius: { card: "18px", btn: "12px", field: "12px", pill: "999px" },
      boxShadow: { card: "var(--shadow)", pop: "var(--shadow-pop)" },
      backgroundImage: { mark: "var(--gradient-mark)" },
      fontSize: {
        base: ["14px", "22px"],
        page: ["24px", "32px"],
        sub: ["13px", "20px"],
        card: ["15px", "22px"],
        kpi: ["30px", "36px"],
      },
      transitionDuration: { DEFAULT: "150ms" },
    },
  },
  plugins: [],
};
