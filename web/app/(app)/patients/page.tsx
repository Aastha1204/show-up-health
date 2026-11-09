"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Empty, inputCls } from "@/components/ui";
import { api } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { fmtDay, istDate } from "@/lib/time";
import { CONSENT_LABEL, LANGUAGE_LABEL, type Patient } from "@/lib/types";

const PAGE = 50;

export default function PatientsPage() {
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const q = useDebounced(search.trim(), 250);
  const list = useQuery({
    queryKey: ["patients", q, limit], queryFn: () => api<Patient[]>(`/patients?q=${encodeURIComponent(q)}&limit=${limit}`),
    placeholderData: (prev) => prev,
  });
  const rows = list.data ?? [];

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Patients</h1>
          <p className="text-xs text-[var(--muted)]">Consent is recorded for every patient. Opted-out patients receive no messages of any kind.</p>
        </div>
        <input value={search} onChange={(e) => { setSearch(e.target.value); setLimit(PAGE); }} placeholder="Search name or phone…" aria-label="Search patients" className={`${inputCls} max-w-xs`} />
      </div>

      {list.isLoading ? <div className="h-60 animate-pulse rounded-xl bg-black/5" />
        : rows.length === 0 ? <Empty>No patients match “{search}”.</Empty>
        : (
          <div className="overflow-x-auto rounded-xl border border-[var(--line)] bg-[var(--surface)]">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="border-b border-[var(--line)] text-left text-xs uppercase tracking-wide text-[var(--muted)]">
                <tr><th className="px-4 py-2.5">Patient</th><th>Phone</th><th>Language</th><th>Consent</th><th>WhatsApp</th><th className="pr-4 text-right">No-shows</th></tr>
              </thead>
              <tbody className="divide-y divide-[var(--line)]">
                {rows.map((p) => (
                  <tr key={p.id} data-patient={p.name}>
                    <td className="px-4 py-2.5 font-medium">
                      {p.name}
                      {p.is_demo_seed && <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:text-amber-200" title="Simulated data: never messaged on real WhatsApp">Seeded</span>}
                    </td>
                    <td className="tabular-nums text-[var(--muted)]">{p.phone}</td>
                    <td>{LANGUAGE_LABEL[p.language]}</td>
                    <td className="text-[var(--muted)]">{CONSENT_LABEL[p.consent_source]} · {fmtDay(istDate(p.consent_at))}</td>
                    <td>{p.opted_out
                      ? <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-700 dark:text-red-300" title={p.opted_out_at ? `Opted out ${fmtDay(istDate(p.opted_out_at))}` : undefined}>Opted out</span>
                      : <span className="text-[var(--muted)]">Active</span>}</td>
                    <td className="pr-4 text-right tabular-nums">
                      {p.no_show_count >= 2 ? <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-semibold text-red-700 dark:text-red-300" title="Two or more no-shows">⚠ {p.no_show_count}</span> : p.no_show_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      <div className="mt-3 flex items-center justify-between text-xs text-[var(--muted)]">
        <span>Showing {rows.length}{rows.length === limit ? "+" : ""} patients</span>
        {rows.length === limit && <button className="rounded-lg border border-[var(--line)] px-3 py-1.5 text-sm font-medium text-[var(--ink)] hover:bg-black/5" onClick={() => setLimit(limit + PAGE)}>Load more</button>}
      </div>
    </div>
  );
}
