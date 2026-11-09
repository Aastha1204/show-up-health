"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post } from "@/lib/api";
import { fmtTime } from "@/lib/time";
import type { Appt } from "@/lib/types";
import { useToast } from "./Toasts";

function until(iso: string, now: number) {
  const m = Math.max(0, Math.round((new Date(iso).getTime() - now) / 60000));
  return m < 60 ? `in ${m} min` : `in ${Math.floor(m / 60)} h ${m % 60 ? `${m % 60} min` : ""}`.trim();
}

export default function CallList({ items, nowMs, onOpen }: { items: Appt[]; nowMs: number; onOpen: (a: Appt) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const confirm = useMutation({
    mutationFn: (a: Appt) => post(`/appointments/${a.id}/status`, { status: "confirmed", via: "phone" }),
    onSuccess: (_d, a) => { toast(`${a.patient_name} confirmed by phone`, "success"); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "error"),
  });

  return (
    <section aria-label="Call these patients" className="rounded-xl border border-[var(--line)] bg-[var(--surface)]">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <h2 className="text-sm font-semibold">Call these patients</h2>
        <p className="text-xs text-[var(--muted)]">Not yet confirmed, starting in the next 12 hours</p>
      </div>
      {items.length === 0 ? (
        <p className="p-4 text-sm text-[var(--muted)]">Everyone starting soon has confirmed. 🎉</p>
      ) : (
        <ul className="divide-y divide-[var(--line)]">
          {items.map((a) => (
            <li key={a.id} className="space-y-2 px-4 py-3">
              <div className="flex items-start justify-between gap-2">
                <button onClick={() => onOpen(a)} className="min-w-0 text-left">
                  <div className="truncate text-sm font-semibold">{a.patient_name}</div>
                  <div className="text-xs text-[var(--muted)]">{fmtTime(a.start_at)} · {until(a.start_at, nowMs)} · {a.doctor_name}</div>
                </button>
                {a.patient_opted_out && <span title="Opted out of WhatsApp" className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:text-amber-200">Phone only</span>}
              </div>
              <div className="flex gap-2">
                <a href={`tel:${a.patient_phone}`} className="flex-1 rounded-lg border border-[var(--line)] px-3 py-2 text-center text-sm font-medium hover:bg-black/5" aria-label={`Call ${a.patient_name} on ${a.patient_phone}`}>
                  📞 {a.patient_phone}
                </a>
                <button disabled={confirm.isPending} onClick={() => confirm.mutate(a)} className="rounded-lg bg-teal-600 px-3 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-60">
                  Called — confirmed
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
