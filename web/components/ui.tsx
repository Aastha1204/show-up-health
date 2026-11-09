"use client";

import { useEffect, useRef } from "react";
import { STATUS_LABEL, type Status } from "@/lib/types";
import { useNow } from "@/lib/hooks";

export function StatusChip({ status, className = "" }: { status: Status; className?: string }) {
  return (
    <span className={`st st-${status} inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold leading-none ${className}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

/** Accessible modal: Escape closes, focus moves in, backdrop click closes. */
export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/50 p-3" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className={`my-6 w-full ${wide ? "max-w-2xl" : "max-w-lg"} rounded-2xl border border-[var(--line)] bg-[var(--surface)] shadow-2xl outline-none`}>
        <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-3.5">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-md px-2 text-xl leading-none text-[var(--muted)] hover:bg-black/5">×</button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, error, children, hint }: { label: string; error?: string | null; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-[var(--muted)]">{hint}</span>}
      {error && <span role="alert" className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  );
}

export const inputCls = "w-full rounded-lg border border-[var(--line)] bg-transparent px-3 py-2 text-sm disabled:opacity-60";
export const btnPrimary = "rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-50";
export const btnGhost = "rounded-lg border border-[var(--line)] px-3 py-2 text-sm font-medium hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50";

/** mm:ss until `to` (an ISO instant). */
export function Countdown({ to, className = "" }: { to: string; className?: string }) {
  const now = useNow(1000);
  const left = Math.max(0, Math.floor((new Date(to).getTime() - now) / 1000));
  const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  return <span className={`tabular-nums ${left < 60 ? "font-semibold text-red-600" : ""} ${className}`} aria-label={`expires in ${text}`}>{left === 0 ? "expiring…" : text}</span>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border border-dashed border-[var(--line)] p-8 text-center text-sm text-[var(--muted)]">{children}</div>;
}
