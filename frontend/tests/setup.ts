import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { setupI18n } from "@/lib/i18n";

afterEach(() => {
  cleanup();
});

// jsdom has no matchMedia; the app only reads it for reduced-motion.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
});

await setupI18n();

// jsdom has no layout, so `scrollIntoView` is missing entirely. The inbox calls it when the newest message
// changes (R15 P1-A: a thread that only scrolls for *new* messages, never when an older page is prepended),
// and a missing method must not read as a React error in a test that is not about scrolling.
Element.prototype.scrollIntoView = vi.fn();
