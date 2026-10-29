/**
 * Small accessible dialog used by the confirmations (convert, lost, redistribute, import).
 *
 * Two things here are load-bearing, both found by the phone e2e project in R3:
 *  - the panel may be taller than the viewport (the import preview is a table plus a stats row), so the
 *    overlay scrolls and the panel is capped at the *visible* height. A `place-items-center` overlay with an
 *    uncapped card grew past the screen and the footer's «تنفيذ الاستيراد» sat outside the viewport — the
 *    click retried for 60 s and the test timed out with the element "visible, enabled and stable";
 *  - the footer is `sticky` to the panel's own scroll box, so the confirming button stays reachable without
 *    hunting for the bottom of a long report.
 */
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { Card } from "@/components/ui";

export function Modal({
  title,
  children,
  actions,
  onClose,
  width = 480,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  width?: number;
}) {
  const { t } = useTranslation();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-center overflow-y-auto overscroll-contain bg-[rgba(16,24,40,.5)] p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <Card
        className="flex max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden"
        style={{ maxWidth: width }}
      >
        <header className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-card font-semibold text-ink">{title}</h2>
          <button
            type="button"
            className="grid h-8 w-8 place-items-center rounded-full text-muted transition-colors duration-150 hover:bg-[#F3F4F9] hover:text-ink"
            onClick={onClose}
            // «إغلاق» is also the footer action's own label, so an accessible query for the dialog's close
            // button resolved to two elements and failed in strict mode — the X is named for what it does
            aria-label={t("common.closeDialog")}
          >
            <X size={16} strokeWidth={2} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {actions ? <footer className="mt-5 flex shrink-0 items-center justify-end gap-2">{actions}</footer> : null}
      </Card>
    </div>
  );
}
