/** Toast host — short-lived confirmations after an optimistic update (spec §4). */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, TriangleAlert } from "lucide-react";

type ToastTone = "ok" | "warn" | "error";
interface ToastItem {
  id: number;
  text: string;
  tone: ToastTone;
}

type Push = (text: string, tone?: ToastTone) => void;

const ToastContext = createContext<Push>(() => undefined);

export function useToast(): Push {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const push = useCallback<Push>((text, tone = "ok") => {
    seq.current += 1;
    const id = seq.current;
    setItems((current) => [...current, { id, text, tone }]);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 z-50 flex flex-col items-start gap-2" style={{ insetInlineEnd: 24 }} role="status" aria-live="polite">
        {items.map((item) => (
          <Toast key={item.id} item={item} onDone={() => setItems((current) => current.filter((row) => row.id !== item.id))} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const STYLE: Record<ToastTone, { bg: string; ink: string }> = {
  ok: { bg: "var(--g-bg)", ink: "var(--g-ink)" },
  warn: { bg: "var(--a-bg)", ink: "var(--a-ink)" },
  error: { bg: "var(--r-bg)", ink: "var(--r-ink)" },
};

function Toast({ item, onDone }: { item: ToastItem; onDone: () => void }) {
  const [visible, setVisible] = useState(false);
  const style = useMemo(() => STYLE[item.tone], [item.tone]);

  useEffect(() => {
    const raf = window.requestAnimationFrame(() => setVisible(true));
    const hide = window.setTimeout(() => setVisible(false), 3200);
    const done = window.setTimeout(onDone, 3450);
    return () => {
      window.cancelAnimationFrame(raf);
      window.clearTimeout(hide);
      window.clearTimeout(done);
    };
  }, [onDone]);

  return (
    <div
      className="pointer-events-auto flex max-w-[380px] items-center gap-2 rounded-card px-4 py-2.5 text-[13px] font-semibold shadow-card transition-all duration-150"
      style={{ background: style.bg, color: style.ink, opacity: visible ? 1 : 0, transform: visible ? "translateY(0)" : "translateY(6px)" }}
    >
      {item.tone === "error" ? <TriangleAlert size={16} strokeWidth={2} /> : <CheckCircle2 size={16} strokeWidth={2} />}
      {item.text}
    </div>
  );
}
