"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useLive } from "@/lib/live";
import { fmtStamp, fmtTime } from "@/lib/time";
import { STATUS_LABEL, type LogItem } from "@/lib/types";

function Transport({ t }: { t: "simulator" | "live" }) {
  return t === "live"
    ? <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase text-emerald-700 dark:text-emerald-300">Live</span>
    : <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-700 dark:text-amber-300">Simulator</span>;
}

function Ticks({ m }: { m: Extract<LogItem, { type: "message" }> }) {
  const { sent, delivered, read, failed } = m.ticks;
  return (
    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--muted)]">
      {sent && <span>✓ Sent {fmtTime(sent)}</span>}
      {delivered && <span>✓✓ Delivered {fmtTime(delivered)}</span>}
      {read && <span className="font-medium text-sky-500">✓✓ Read {fmtTime(read)}</span>}
      {(failed || m.status === "failed") && <span className="font-medium text-red-600">✕ Failed{m.error ? `: ${m.error}` : ""}</span>}
    </div>
  );
}

export default function MessageLog({ appointmentId }: { appointmentId: string }) {
  const live = useLive();
  const q = useQuery({ queryKey: ["log", appointmentId], queryFn: () => api<LogItem[]>(`/appointments/${appointmentId}/log`), refetchInterval: live.pollMs });
  if (q.isLoading) return <p className="text-sm text-[var(--muted)]">Loading…</p>;
  if (q.isError) return <p role="alert" className="text-sm text-red-600">Could not load the message log.</p>;
  const items = q.data ?? [];
  if (!items.length) return <p className="text-sm text-[var(--muted)]">Nothing yet.</p>;

  return (
    <ol className="space-y-3 border-l border-[var(--line)] pl-4" aria-label="Message log">
      {items.map((it) => (
        <li key={`${it.type}-${it.id}`} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-[var(--surface)] bg-teal-600" aria-hidden />
          <div className="mb-0.5 text-[11px] text-[var(--muted)]">{fmtStamp(it.at)}</div>
          {it.type === "message" && (
            <div className="rounded-lg border border-[var(--line)] p-2.5 text-sm">
              <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
                <span>Sent to patient{it.template_name ? ` · ${it.template_name.replace("showup_", "").replaceAll("_", " ")}` : it.kind === "interactive_list" ? " · slot list" : " · reply"}</span>
                <Transport t={it.transport} />
              </div>
              <p className="whitespace-pre-wrap break-words">{it.body}</p>
              {it.footer && <p className="mt-1 text-xs text-[var(--muted)]">{it.footer}</p>}
              {it.buttons && <div className="mt-2 flex flex-wrap gap-1">{it.buttons.map((b) => <span key={b.text} className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px]">{b.text}</span>)}</div>}
              <Ticks m={it} />
            </div>
          )}
          {it.type === "reply" && (
            <div className="rounded-lg bg-teal-600/10 p-2.5 text-sm">
              <span className="text-[11px] text-[var(--muted)]">Patient {it.via === "text" ? "wrote" : it.via === "list_reply" ? "chose from the list" : "tapped"}</span>
              <p className="font-medium">“{it.text || it.payload}”</p>
            </div>
          )}
          {it.type === "status" && (
            <div className="text-sm">
              {it.from_status ? <>Status <b>{STATUS_LABEL[it.from_status]}</b> → <b>{STATUS_LABEL[it.to_status]}</b></> : <>Appointment created as <b>{STATUS_LABEL[it.to_status]}</b></>}
              <span className="text-[var(--muted)]"> · by {it.actor}{it.detail?.via === "phone" ? " (called)" : ""}</span>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
