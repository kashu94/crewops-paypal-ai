"use client";

import { Check, ChevronsUpDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { control, cx } from "./ui";

export type ComboOption = { value: string; label: ReactNode; hint?: string; search?: string };

/** A list to pick from, with a search box: the value is always one of the options, so a form
 * can never be filed with an id that does not exist. The value is also posted as ``name``. */
export function Combo({
  label,
  name,
  options,
  value,
  onChange,
  placeholder = "Search…",
  hint,
  required,
  className,
}: {
  label: ReactNode;
  name?: string;
  options: ComboOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: ReactNode;
  required?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useId();
  const chosen = options.find((o) => o.value === value);

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const text = (o: ComboOption) => `${o.value} ${o.search ?? ""} ${typeof o.label === "string" ? o.label : ""} ${o.hint ?? ""}`.toLowerCase();
    return options.filter((o) => words.every((w) => text(o).includes(w))).slice(0, 60);
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function pick(o: ComboOption) {
    onChange(o.value);
    setOpen(false);
    setQuery("");
  }

  return (
    <div ref={box} className={cx("relative flex min-w-0 flex-col gap-1 text-[13px]", className)}>
      <span className="font-medium text-foreground">{label}</span>
      {name && <input type="hidden" name={name} value={value} />}
      <button
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-controls={list}
        onClick={() => setOpen((o) => !o)}
        className={cx(control, "flex items-center justify-between gap-2 text-left", !chosen && "text-faint")}
      >
        <span className="min-w-0 truncate">{chosen ? chosen.label : required ? "Choose…" : "Any"}</span>
        <ChevronsUpDown size={14} className="shrink-0 text-faint" />
      </button>
      {required && !value && <input tabIndex={-1} aria-hidden required value="" onChange={() => undefined} className="pointer-events-none absolute bottom-0 left-4 h-px w-px opacity-0" />}
      {hint && <span className="text-xs text-faint">{hint}</span>}
      {open && (
        <div className="animate-rise absolute left-0 right-0 top-full z-40 mt-1 overflow-hidden rounded-lg border border-line bg-surface shadow-[0_12px_28px_rgb(0_0_0/0.14)]">
          <label className="flex items-center gap-2 border-b border-rule px-3">
            <Search size={14} className="text-faint" />
            <input
              autoFocus
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, shown.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  if (shown[active]) pick(shown[active]);
                } else if (e.key === "Escape") setOpen(false);
              }}
              placeholder={placeholder}
              className="h-9 w-full bg-transparent text-sm outline-none placeholder:text-faint"
            />
          </label>
          <ul id={list} role="listbox" className="max-h-64 overflow-y-auto py-1">
            {shown.length === 0 && <li className="px-3 py-2 text-sm text-muted">Nothing matches.</li>}
            {shown.map((o, i) => (
              <li
                key={o.value}
                role="option"
                aria-selected={o.value === value}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(o);
                }}
                className={cx("flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm", i === active && "bg-sunken")}
              >
                <Check size={13} className={cx("shrink-0", o.value === value ? "text-accent" : "invisible")} />
                <span className="min-w-0 flex-1 truncate">{o.label}</span>
                {o.hint && <span className="shrink-0 font-mono text-[11px] text-faint">{o.hint}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
