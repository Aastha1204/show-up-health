"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Phone from "@/components/Phone";
import { Empty } from "@/components/ui";
import { api } from "@/lib/api";
import { useLive } from "@/lib/live";
import { istTime, type Thread } from "@/lib/sim";
import type { Reference } from "@/lib/types";

export const chatKey = (kind: "patient" | "doctor", id: string) => `${kind}:${id}`;
const keyOf = (t: Thread) => chatKey(t.kind, t.id);

/** The patient's phone plus a chat picker. `compact` swaps the chat list for a dropdown (the presenter view).
 *  `focus` jumps to a chat on purpose (e.g. after the presenter creates a prospect); otherwise the phone stays on the
 *  chat that was chosen, and chats with new activity get a dot. */
export default function SimulatorPane({ compact = false, focus = null }: { compact?: boolean; focus?: { key: string; n: number } | null }) {
  const live = useLive();
  const [selected, setSelected] = useState<string | null>(null);
  const [seen, setSeen] = useState<Record<string, string>>({});

  const threads = useQuery({ queryKey: ["sim-threads"], queryFn: () => api<Thread[]>("/simulator/threads"), refetchInterval: live.pollMs });
  const ref = useQuery({ queryKey: ["reference"], queryFn: () => api<Reference>("/reference"), staleTime: 60_000 });
  const list = threads.data ?? [];
  const current = list.find((t) => keyOf(t) === selected);

  useEffect(() => { if (focus) setSelected(focus.key); }, [focus]);   // `n` changes on every request, so the same chat can be re-focused
  useEffect(() => { if (!selected && list.length) setSelected(keyOf(list[0])); }, [selected, list]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (current) setSeen((s) => (s[keyOf(current)] === current.last_at ? s : { ...s, [keyOf(current)]: current.last_at }));
  }, [current?.last_at, current?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const fresh = (t: Thread) => !(current && keyOf(t) === keyOf(current)) && seen[keyOf(t)] !== t.last_at;

  const phone = current ? (
    <>
      <Phone kind={current.kind} id={current.id} clinicName={ref.data?.clinic.name ?? "Clinic"} />
      <p className="mt-3 text-center text-xs text-[var(--muted)]">
        Viewing as {current.name}{current.phone ? ` · ${current.phone.slice(0, 3)} •••••• ${current.phone.slice(-4)}` : ""}
      </p>
    </>
  ) : <div className="mx-auto flex h-[400px] w-[300px] max-w-full items-center justify-center rounded-[2rem] border-[10px] border-neutral-900 bg-[#efeae2] p-6 text-center text-sm text-gray-500">No conversations yet</div>;

  if (compact) {
    return (
      <section aria-label="WhatsApp Simulator">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">
          Chat
          <select value={current ? keyOf(current) : ""} onChange={(e) => setSelected(e.target.value)} aria-label="Chat"
            className="mt-1 w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm font-normal normal-case tracking-normal text-[var(--ink)]">
            {list.length === 0 && <option value="">No conversations yet</option>}
            {list.map((t) => (
              <option key={keyOf(t)} value={keyOf(t)}>{fresh(t) ? "● " : ""}{t.kind === "doctor" ? `${t.name} (doctor summary)` : t.name}</option>
            ))}
          </select>
        </label>
        {phone}
      </section>
    );
  }

  return (
    <div className="grid gap-6 md:grid-cols-[280px_1fr]">
      <section aria-label="Chats">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted)]">Chats</h2>
        {threads.isLoading && <p className="text-sm text-[var(--muted)]">Loading…</p>}
        {!threads.isLoading && list.length === 0 && <Empty>No conversations yet. Book an appointment (or fire a reminder) and its chat appears here.</Empty>}
        <ul className="space-y-1.5">
          {list.map((t) => {
            const active = !!current && keyOf(t) === keyOf(current);
            return (
              <li key={keyOf(t)}>
                <button onClick={() => setSelected(keyOf(t))} aria-current={active ? "true" : undefined}
                  className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${active ? "border-teal-600 bg-teal-600/10" : "border-[var(--line)] bg-[var(--surface)] hover:bg-black/5"}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5">
                      {fresh(t) && <span role="img" aria-label="new activity" className="h-2 w-2 shrink-0 rounded-full bg-teal-500" />}
                      <span className="truncate text-sm font-medium">{t.name}</span>
                    </span>
                    <span className="shrink-0 text-[11px] text-[var(--muted)]">{istTime(t.last_at)}</span>
                  </div>
                  <div className="truncate text-xs text-[var(--muted)]">{t.last_body}</div>
                  <div className="mt-1 flex gap-1">
                    {t.kind === "doctor" && <span className="rounded bg-indigo-500/15 px-1.5 text-[10px] font-medium text-indigo-600">Doctor summary</span>}
                    {t.is_demo_seed && <span className="rounded bg-amber-500/15 px-1.5 text-[10px] font-medium text-amber-700">Seeded</span>}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <section aria-label="Phone" className="pb-10">{phone}</section>
    </div>
  );
}
