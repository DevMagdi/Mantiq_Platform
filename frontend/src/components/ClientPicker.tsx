/**
 * A client **search**, not a client **dump** (R15 P2).
 *
 * Two dialogs (post a ledger entry, create a policy draft) opened by fetching `page_size: 200` clients and
 * rendering them in a `<select>`. The office has 500 companies today; the fetch grows with the customer list,
 * the dialog gets slower every month, and the operator still has to scroll a list of 200 to find one name.
 *
 * This is a debounced typeahead over the same endpoint (which is scope-aware by construction, so a rep cannot
 * use it to browse somebody else's book): two characters, 300 ms after the last keystroke, at most 20 rows.
 * The selected client stays visible as a chip so the field never renders blank after a search is cleared.
 */
import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "@/lib/api/client";

interface PickerRow {
  id: string;
  name: string;
}

const DEBOUNCE_MS = 300;
const PAGE = 20;
const MIN_CHARS = 2;

export function ClientPicker({
  value,
  onChange,
  placeholder,
  autoFocus,
  id,
}: {
  /** selected client id, or "" */
  value: string;
  onChange: (id: string, name: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** the input's id, so the dialog's own `<label htmlFor>` keeps working */
  id?: string;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<PickerRow[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  // a stale response must not overwrite a newer one: every keystroke bumps the token it was sent with
  const token = useRef(0);

  useEffect(() => {
    const clean = query.trim();
    if (value && !clean) return; // a selection without an active search shows the chip, not the whole list
    if (clean.length < MIN_CHARS) {
      setRows([]);
      setBusy(false);
      return;
    }
    setBusy(true);
    window.clearTimeout(timer.current);
    const mine = ++token.current;
    timer.current = window.setTimeout(() => {
      api
        .get<{ items: PickerRow[] }>("/clients", { q: clean, page_size: PAGE })
        .then((page) => {
          if (mine === token.current) setRows(page.items);
        })
        .catch(() => undefined)
        .finally(() => {
          if (mine === token.current) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer.current);
  }, [query, value]);

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-btn border border-line bg-surface px-3 py-2" data-testid="client-picker-selected">
        <span className="text-[13px] font-semibold text-ink">{selectedName || t("clientPicker.selected")}</span>
        <button
          type="button"
          className="ms-auto rounded-pill p-1 text-muted hover:text-ink"
          aria-label={t("clientPicker.clear")}
          onClick={() => {
            setSelectedName("");
            setQuery("");
            setRows([]);
            onChange("", "");
          }}
        >
          <X size={14} strokeWidth={2} />
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="relative">
        <Search size={14} strokeWidth={1.8} className="pointer-events-none absolute inset-y-0 start-3 my-auto text-muted" />
        <input
          id={id}
          className="field ps-9"
          value={query}
          autoFocus={autoFocus}
          placeholder={placeholder ?? t("clientPicker.search")}
          onChange={(event) => setQuery(event.target.value)}
          aria-label={t("clientPicker.search")}
          data-testid="client-picker-input"
        />
      </div>
      {query.trim().length >= MIN_CHARS ? (
        <ul className="mt-1.5 max-h-48 overflow-auto rounded-btn border border-line bg-surface py-1" role="listbox">
          {busy && rows.length === 0 ? <li className="px-3 py-2 text-[12.5px] text-muted">{t("common.loading")}</li> : null}
          {!busy && rows.length === 0 ? <li className="px-3 py-2 text-[12.5px] text-muted">{t("clientPicker.empty")}</li> : null}
          {rows.map((row) => (
            <li key={row.id} role="presentation">
              {/* the option *is* the button: a keyboard user tabs to it, and a click and Enter do the same thing */}
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="w-full px-3 py-2 text-start text-[13px] text-ink hover:bg-[var(--color-track)]"
                onClick={() => {
                  setSelectedName(row.name);
                  setQuery("");
                  setRows([]);
                  onChange(row.id, row.name);
                }}
              >
                {row.name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
