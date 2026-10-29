/** Design tokens are CSS variables (spec §4) so the palette has exactly one source of truth. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        card: "var(--card)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        line: "var(--line)",
        "line-2": "var(--line-2)",
        sidebar: "var(--sidebar)",
        "sidebar-active": "var(--sidebar-active)",
        "sidebar-text": "var(--sidebar-text)",
        primary: { DEFAULT: "var(--primary)", hover: "var(--primary-hover)", soft: "var(--primary-soft)", ink: "var(--primary-ink)" },
        soft: "var(--soft-bg)",
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
      boxShadow: { card: "var(--shadow)", pop: "0 12px 32px rgba(16,24,40,.12)" },
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
