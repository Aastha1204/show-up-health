"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useToast } from "@/components/Toasts";
import { btnGhost, btnPrimary, Empty, inputCls } from "@/components/ui";
import { api, post } from "@/lib/api";
import { useLive } from "@/lib/live";
import { relTime } from "@/lib/time";
import type { InboxItem } from "@/lib/types";

const WINDOW_CLOSED = "The 24-hour WhatsApp window is closed: this patient has not messaged in the last 24 hours, so only approved templates can be sent. Phone them instead.";

function Item({ item }: { item: InboxItem }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const canReply = item.window_open && !item.patient_opted_out && !item.handled;
  const why = item.patient_opted_out ? "This patient opted out of WhatsApp updates." : WINDOW_CLOSED;

  const handled = useMutation({
    mutationFn: (h: boolean) => post(`/inbox/${item.id}/handled`, { handled: h }),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e) => toast((e as Error).message, "error"),
  });
  const reply = useMutation({
    mutationFn: () => post(`/inbox/${item.id}/reply`, { text }),
    onSuccess: () => { toast(`Reply sent to ${item.patient_name}`, "success"); setText(""); setErr(null); qc.invalidateQueries(); },
    onError: (e) => setErr((e as Error).message),
  });

  return (
    <li className={`rounded-xl border bg-[var(--surface)] p-4 ${item.handled ? "border-[var(--line)] opacity-70" : "border-amber-500/50"}`} data-inbox={item.patient_name}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{item.patient_name} <a href={`tel:${item.patient_phone}`} className="text-xs font-normal text-teal-700 underline dark:text-teal-300">{item.patient_phone}</a></div>
          <div className="text-xs text-[var(--muted)]">{relTime(item.received_at)}</div>
        </div>
        <button className={`${btnGhost} !px-3 !py-1.5 text-xs`} disabled={handled.isPending} onClick={() => handled.mutate(!item.handled)}>
          {item.handled ? "Reopen" : "Mark handled"}
        </button>
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-black/5 px-3 py-2 text-sm">{item.text}</p>

      {!item.handled && (
        <form className="mt-3" onSubmit={(e) => { e.preventDefault(); if (text.trim()) reply.mutate(); }}>
          <div className="flex gap-2" title={canReply ? undefined : why}>
            <input value={text} onChange={(e) => setText(e.target.value)} disabled={!canReply} maxLength={1000} aria-label={`Reply to ${item.patient_name}`}
              placeholder={canReply ? "Reply on WhatsApp…" : "Reply unavailable"} className={inputCls} />
            <button className={btnPrimary} disabled={!canReply || !text.trim() || reply.isPending} title={canReply ? undefined : why}>{reply.isPending ? "Sending…" : "Send"}</button>
          </div>
          {!canReply && <p className="mt-1.5 text-xs text-[var(--muted)]">{why}</p>}
          {canReply && <p className="mt-1.5 text-xs text-[var(--muted)]">Free-form reply, available for 24 hours after the patient&apos;s message. Do not mention treatments or medical details.</p>}
          {err && <p role="alert" className="mt-1.5 text-xs text-red-600">{err}</p>}
        </form>
      )}
    </li>
  );
}

export default function InboxPage() {
  const live = useLive();
  const [tab, setTab] = useState<"open" | "handled">("open");
  const q = useQuery({ queryKey: ["inbox", tab], queryFn: () => api<InboxItem[]>(`/inbox?status=${tab}`), refetchInterval: live.pollMs });
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Inbox</h1>
          <p className="text-xs text-[var(--muted)]">Messages patients typed (not button taps) and requests they could not complete on their own.</p>
        </div>
        <div className="flex rounded-lg border border-[var(--line)] p-0.5 text-sm" role="tablist">
          {(["open", "handled"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded-md px-3 py-1.5 font-medium ${tab === t ? "bg-teal-600 text-white" : "text-[var(--muted)]"}`}>{t === "open" ? "Open" : "Handled"}</button>
          ))}
        </div>
      </div>
      {q.isLoading ? <div className="h-32 animate-pulse rounded-xl bg-black/5" />
        : !q.data?.length ? <Empty>{tab === "open" ? "Nothing waiting for you. 🎉" : "No handled messages yet."}</Empty>
        : <ul className="space-y-3">{q.data.map((i) => <Item key={i.id} item={i} />)}</ul>}
    </div>
  );
}
