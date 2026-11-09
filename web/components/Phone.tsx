"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import { buttonTap, listReply, textMessage } from "@/lib/meta";
import { istDay, istDayKey, istTime, type ListRow, type Msg, type ThreadMessages } from "@/lib/sim";

function Ticks({ status }: { status?: Msg["status"] }) {
  if (status === "failed") return <span className="text-red-500 font-bold" title="Failed">!</span>;
  const double = status === "delivered" || status === "read";
  return (
    <span className={status === "read" ? "text-[#53bdeb]" : "text-gray-400"} aria-label={`status: ${status}`}>
      {double ? "✓✓" : "✓"}
    </span>
  );
}

function ListIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
    </svg>
  );
}

export default function Phone({ kind, id, clinicName }: { kind: "patient" | "doctor"; id: string; clinicName: string }) {
  const qc = useQueryClient();
  const key = ["sim-msgs", kind, id];
  const q = useQuery({
    queryKey: key,
    queryFn: () => api<ThreadMessages>(`/simulator/threads/${kind}/${id}/messages`),
    refetchInterval: 1500,
  });
  const phone = q.data?.recipient.phone ?? null;
  const [sheet, setSheet] = useState<Msg | null>(null);
  const [picked, setPicked] = useState<ListRow | null>(null);
  const [used, setUsed] = useState<Set<string>>(new Set());   // greys a button out the instant it is tapped
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const inbound = useMutation({
    mutationFn: (payload: unknown) => post("/simulator/inbound", payload),
    onSettled: () => qc.invalidateQueries({ queryKey: key }),
  });

  const messages = q.data?.messages ?? [];

  // Chat is open => everything the clinic sent turns "read" (blue ticks).
  const unread = useMemo(
    () => messages.filter((m) => m.direction === "out" && m.status !== "read" && m.status !== "failed").map((m) => m.id).join(","),
    [messages],
  );
  const lastMarked = useRef("");
  useEffect(() => {
    if (!unread || unread === lastMarked.current || document.hidden) return;
    lastMarked.current = unread;
    post(`/simulator/threads/${kind}/${id}/read`).then(() => qc.invalidateQueries({ queryKey: key })).catch(() => {});
  }, [unread, kind, id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  useEffect(() => { setSheet(null); setPicked(null); setUsed(new Set()); lastMarked.current = ""; }, [kind, id]);

  const tapButton = (m: Msg, b: { payload: string; text: string }) => {
    if (!phone) return;
    setUsed((s) => new Set(s).add(m.id));
    inbound.mutate(buttonTap(phone, b.payload, b.text, m.wa_message_id));
  };

  const sendPick = () => {
    if (!phone || !sheet || !picked) return;
    setUsed((s) => new Set(s).add(sheet.id));
    inbound.mutate(listReply(phone, picked.id, picked.title, picked.description ?? "", sheet.wa_message_id));
    setSheet(null);
    setPicked(null);
  };

  const sendText = (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim();
    if (!t || !phone) return;
    inbound.mutate(textMessage(phone, t));
    setDraft("");
  };

  let lastDay = "";
  return (
    <div className="relative mx-auto w-[360px] max-w-full rounded-[2.2rem] border-[10px] border-neutral-900 bg-neutral-900 shadow-2xl">
      <div className="relative flex h-[min(680px,calc(100vh-9rem))] min-h-[420px] flex-col overflow-hidden rounded-[1.5rem]">
        {/* header */}
        <div className="flex items-center gap-3 bg-teal-700 px-3 py-2.5 text-white">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-100 text-sm font-bold text-teal-800">
            {clinicName.slice(0, 1)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{clinicName}</div>
            <div className="text-[11px] text-teal-100">Business account</div>
          </div>
          <span className="rounded-full bg-white/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide" title="This is a simulation, not WhatsApp">
            Simulator
          </span>
        </div>

        {/* chat */}
        <div ref={scroller} className="sim-screen flex-1 space-y-2 overflow-y-auto px-3 py-3" aria-live="polite">
          {q.isLoading && <p className="pt-10 text-center text-xs text-gray-500">Loading…</p>}
          {!q.isLoading && messages.length === 0 && (
            <p className="pt-10 text-center text-xs text-gray-500">No messages yet. Fire a reminder to start the conversation.</p>
          )}
          {messages.map((m) => {
            const day = istDayKey(m.created_at);
            const showDay = day !== lastDay;
            lastDay = day;
            const mine = m.direction === "in";                     // the patient's own replies sit on the right
            const spent = !!m.consumed || used.has(m.id);
            return (
              <div key={m.id}>
                {showDay && (
                  <div className="my-2 text-center">
                    <span className="rounded-md bg-white/80 px-2 py-0.5 text-[11px] text-gray-600 shadow-sm">{istDay(m.created_at)}</span>
                  </div>
                )}
                <div className={`bubble-in flex ${mine ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] rounded-lg text-[13px] leading-snug text-gray-900 shadow-sm ${mine ? "bg-[#d9fdd3]" : "bg-white"}`}>
                    <div className="px-2.5 pt-1.5">
                      <p className="whitespace-pre-wrap break-words">{m.body}</p>
                      {m.footer && <p className="mt-1 text-[11px] text-gray-500">{m.footer}</p>}
                      <div className="mb-1 mt-0.5 flex items-center justify-end gap-1 text-[10px] text-gray-500">
                        {m.transport === "live" && <span className="rounded bg-emerald-100 px-1 text-emerald-700">Live</span>}
                        <span>{istTime(m.created_at)}</span>
                        {m.direction === "out" ? <Ticks status={m.status} /> : <span className="text-[#53bdeb]">✓✓</span>}
                      </div>
                    </div>
                    {m.buttons?.map((b) => (
                      <button
                        key={b.payload}
                        disabled={spent || inbound.isPending}
                        onClick={() => tapButton(m, b)}
                        className={`block w-full border-t border-gray-200 px-3 py-2 text-center text-[13px] font-medium transition-colors ${
                          spent ? "cursor-not-allowed text-gray-400" : "text-[#027eb5] hover:bg-sky-50 active:bg-sky-100"
                        }`}
                      >
                        {b.text}
                      </button>
                    ))}
                    {m.list && (
                      <button
                        disabled={spent}
                        onClick={() => { setSheet(m); setPicked(null); }}
                        className={`flex w-full items-center justify-center gap-2 border-t border-gray-200 px-3 py-2 text-[13px] font-medium ${
                          spent ? "cursor-not-allowed text-gray-400" : "text-[#027eb5] hover:bg-sky-50"
                        }`}
                      >
                        <ListIcon /> {m.list.button}
                      </button>
                    )}
                    {m.status === "failed" && m.error && <p className="px-2.5 pb-1.5 text-[10px] text-red-600">{m.error}</p>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* composer */}
        <form onSubmit={sendText} className="flex items-center gap-2 bg-[#f0f2f5] px-2 py-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={kind === "doctor" ? "Doctors don't reply here" : "Type a message (try STOP)"}
            disabled={kind === "doctor" || !phone}
            aria-label="Message"
            className="min-w-0 flex-1 rounded-full bg-white px-4 py-2 text-[13px] text-gray-900 outline-none ring-teal-500 focus:ring-2 disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={!draft.trim() || kind === "doctor"}
            aria-label="Send"
            className="flex h-9 w-9 items-center justify-center rounded-full bg-teal-600 text-white disabled:bg-gray-300"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M2 21l21-9L2 3v7l15 2-15 2z" /></svg>
          </button>
        </form>

        {/* list bottom sheet */}
        {sheet?.list && (
          <div className="absolute inset-0 z-10 flex flex-col justify-end bg-black/40" onClick={() => setSheet(null)}>
            <div className="sheet-up max-h-[78%] overflow-y-auto rounded-t-2xl bg-white" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={sheet.list.header || "Choose an option"}>
              <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
                <h3 className="text-sm font-semibold text-gray-900">{sheet.list.header || "Choose"}</h3>
                <button onClick={() => setSheet(null)} aria-label="Close" className="text-xl leading-none text-gray-500">×</button>
              </div>
              {sheet.list.sections.map((s, i) => (
                <div key={i}>
                  {s.title && <div className="bg-gray-50 px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500">{s.title}</div>}
                  {s.rows.map((r) => (
                    <button key={r.id} onClick={() => setPicked(r)} className="flex w-full items-center gap-3 border-b border-gray-100 px-4 py-2.5 text-left hover:bg-gray-50">
                      <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 ${picked?.id === r.id ? "border-teal-600" : "border-gray-300"}`}>
                        {picked?.id === r.id && <span className="h-2.5 w-2.5 rounded-full bg-teal-600" />}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-[13px] text-gray-900">{r.title}</span>
                        {r.description && <span className="block truncate text-[11px] text-gray-500">{r.description}</span>}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
              <div className="sticky bottom-0 bg-white p-3">
                <button onClick={sendPick} disabled={!picked} className="w-full rounded-full bg-teal-600 py-2.5 text-sm font-semibold text-white disabled:bg-gray-300">
                  Send
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
