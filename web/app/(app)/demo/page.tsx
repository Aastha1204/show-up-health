"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import DayGrid from "@/components/DayGrid";
import SimulatorPane, { chatKey } from "@/components/SimulatorPane";
import { useToast } from "@/components/Toasts";
import { btnGhost, btnPrimary, Empty, inputCls } from "@/components/ui";
import { api, ApiError, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useNow } from "@/lib/hooks";
import { useLive } from "@/lib/live";
import { tenDigits } from "@/components/PatientPicker";
import { useSchedule } from "@/lib/useSchedule";
import { addDays, fmtDay, fmtTime, istDate, weekdayKey } from "@/lib/time";
import type { Language } from "@/lib/types";

type DemoState = {
  mode: "simulator" | "live"; live_ready: boolean; waitlist_ttl_min: number;
  cancel_me: { appointment_id: string; patient_name: string; start_at: string; next_waitlist_patient: string | null } | null;
};
type ProspectResult = { appointment_id: string; patient_id: string; patient_name: string; doctor_name: string; start_at: string; mode: string; confirmation: string; confirmation_reason: string | null };
type CancelResult = { cancelled_patient: string; offered_to: string | null; offered_patient_id: string | null; offer_expires_at: string | null };

const FIRE: { type: "confirmation" | "day_before" | "two_hour" | "post_visit"; label: string }[] = [
  { type: "confirmation", label: "Booking confirmation" }, { type: "day_before", label: "24-hour reminder" },
  { type: "two_hour", label: "2-hour reminder" }, { type: "post_visit", label: "Thank-you message" },
];

function Card({ n, title, children, hint }: { n: string; title: string; children: React.ReactNode; hint?: string }) {
  return (
    <section className="flex flex-col rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4" aria-label={title}>
      <h2 className="text-sm font-semibold"><span className="mr-1.5 inline-grid h-5 w-5 place-items-center rounded-full bg-teal-600 text-[11px] text-white">{n}</span>{title}</h2>
      {hint && <p className="mt-1 text-xs text-[var(--muted)]">{hint}</p>}
      <div className="mt-3 flex flex-1 flex-col gap-2">{children}</div>
    </section>
  );
}

export default function DemoPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const live = useLive();
  const { me } = useAuth();
  const nowMs = useNow(15_000);
  const [date, setDate] = useState(() => istDate());
  const [selected, setSelected] = useState<{ id: string; label: string } | null>(null);
  const [focus, setFocus] = useState<{ key: string; n: number } | null>(null);
  const jump = (key: string) => setFocus((f) => ({ key, n: (f?.n ?? 0) + 1 }));
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [lang, setLang] = useState<Language>("en");
  const [doctorId, setDoctorId] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const { ref, apptsQ, appts, doctors, highlight, counts } = useSchedule(date);
  const state = useQuery({ queryKey: ["demo-state"], queryFn: () => api<DemoState>("/demo/state"), refetchInterval: live.pollMs });
  const st = state.data;
  const phoneOk = /^[6-9]\d{9}$/.test(tenDigits(phone));
  const fail = (e: unknown) => toast(e instanceof ApiError ? e.message : (e as Error).message, "error");

  const prospect = useMutation({
    mutationFn: () => post<ProspectResult>("/demo/prospect", { name: name.trim(), phone: "+91" + tenDigits(phone), language: lang }),
    onSuccess: (r) => {
      const d = istDate(r.start_at);
      setDate(d);
      setSelected({ id: r.appointment_id, label: `${r.patient_name} · ${fmtDay(d)} ${fmtTime(r.start_at)}` });
      jump(chatKey("patient", r.patient_id));
      toast(r.confirmation === "sent"
        ? `${r.patient_name} booked for ${fmtDay(d)}, ${fmtTime(r.start_at)} with ${r.doctor_name}. Confirmation sent${r.mode === "live" ? " to their phone" : " (Simulator)"}.`
        : `Booked, but the confirmation was ${r.confirmation}${r.confirmation_reason ? ` (${r.confirmation_reason.replaceAll("_", " ")})` : ""}.`, r.confirmation === "sent" ? "success" : "error");
      qc.invalidateQueries();
    },
    onError: fail,
  });
  const fire = useMutation({
    mutationFn: (type: string) => post<{ status: string; reason: string | null }>("/demo/fire", { appointment_id: selected!.id, type }),
    onSuccess: (r, type) => { toast(r.status === "sent" ? `${FIRE.find((f) => f.type === type)!.label} sent` : `Not sent: ${(r.reason ?? r.status).replaceAll("_", " ")}`, r.status === "sent" ? "success" : "error"); qc.invalidateQueries(); },
    onError: fail,
  });
  const cancel = useMutation({
    mutationFn: () => post<CancelResult>("/demo/cancel-seeded"),
    onSuccess: (r) => {
      if (r.offered_patient_id) jump(chatKey("patient", r.offered_patient_id));
      toast(r.offered_to ? `${r.cancelled_patient} cancelled. The slot was offered to ${r.offered_to}.` : `${r.cancelled_patient} cancelled. Nobody was offered the slot (no eligible waitlist match, or it is outside 08:00-21:00).`, r.offered_to ? "success" : "info");
      qc.invalidateQueries();
    },
    onError: fail,
  });
  const ttl = useMutation({ mutationFn: (minutes: number) => api("/demo/ttl", { method: "PUT", body: JSON.stringify({ minutes }) }), onSuccess: () => qc.invalidateQueries({ queryKey: ["demo-state"] }), onError: fail });
  const mode = useMutation({
    mutationFn: (m: "live" | "simulator") => api("/demo/mode", { method: "PUT", body: JSON.stringify({ mode: m }) }),
    onSuccess: (_d, m) => { toast(m === "simulator" ? "Simulator mode: messages show only in the phone on the left." : "Live mode on.", "success"); qc.invalidateQueries({ queryKey: ["demo-state"] }); },
    onError: fail,
  });
  const summary = useMutation({
    mutationFn: () => post<{ status: string; doctor_id: string }>("/demo/summary", { doctor_id: doctorId || doctors[0]?.id }),
    onSuccess: (r) => { jump(chatKey("doctor", r.doctor_id)); toast("Doctor's morning summary sent", "success"); qc.invalidateQueries(); },
    onError: fail,
  });
  const reset = useMutation({
    mutationFn: () => post("/demo/reset"),
    onSuccess: () => { setConfirmReset(false); setSelected(null); setDate(istDate()); toast("Demo reset: fresh data for today", "success"); qc.invalidateQueries(); },
    onError: fail,
  });

  const works = (d: string) => doctors.some((doc) => (doc.working_hours?.[weekdayKey(d)] ?? []).length > 0);
  const closed = !!ref.data && !!appts && !works(date) && appts.length === 0;
  const nextWorking = () => { for (let i = 1; i <= 7; i++) if (works(addDays(date, i))) return addDays(date, i); return null; };
  // A closed day is a poor first impression in a sales demo: open on the next working day instead (once, on load).
  const autoJumped = useRef(false);
  useEffect(() => {
    if (autoJumped.current || !closed) return;
    const n = nextWorking();
    if (n) { autoJumped.current = true; setDate(n); }
  }, [closed]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Presenter view</h1>
          <p className="text-xs text-[var(--muted)]">The patient&apos;s phone on the left, the clinic&apos;s live schedule on the right.</p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className={`rounded-full px-2.5 py-1 text-xs font-bold uppercase ${st?.mode === "live" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-800 dark:text-amber-200"}`} data-testid="mode-badge">
            {st?.mode === "live" ? "Live WhatsApp" : "Simulator"}
          </span>
          {me.role === "owner" && <Link href="/analytics" className={btnGhost}>Analytics →</Link>}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Card n="1" title="Add the prospect" hint="Books tomorrow (the next working day) at the next free slot and sends the confirmation now.">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" aria-label="Prospect name" className={inputCls} />
          <div className="flex gap-2">
            <span className="grid place-items-center rounded-lg border border-[var(--line)] px-2.5 text-sm text-[var(--muted)]">+91</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" placeholder="Mobile number" aria-label="Prospect phone" className={inputCls} />
            <select value={lang} onChange={(e) => setLang(e.target.value as Language)} aria-label="Language" className={`${inputCls} !w-24`}>
              <option value="en">EN</option><option value="hi">HI</option><option value="mr">MR</option>
            </select>
          </div>
          <button className={btnPrimary} disabled={!name.trim() || !phoneOk || prospect.isPending} onClick={() => prospect.mutate()}>{prospect.isPending ? "Booking…" : "Book for tomorrow"}</button>
          {st?.mode === "live"
            ? <p className="text-xs text-emerald-700 dark:text-emerald-300">Live: the confirmation goes to this real phone number.</p>
            : <p className="text-xs text-[var(--muted)]">Simulator: the confirmation appears in the phone on the left.</p>}
        </Card>

        <Card n="2" title="Fire a reminder" hint={selected ? undefined : "Click an appointment on the schedule (or add the prospect) to select it."}>
          <div className="rounded-lg bg-black/5 px-3 py-2 text-xs" data-testid="selected-appt">{selected ? <>Selected: <b>{selected.label}</b></> : "No appointment selected"}</div>
          <div className="grid grid-cols-2 gap-2">
            {FIRE.map((f) => <button key={f.type} className={btnGhost} disabled={!selected || fire.isPending} onClick={() => fire.mutate(f.type)}>{f.label}</button>)}
          </div>
          <p className="text-xs text-[var(--muted)]">Sent immediately, ignoring timing and quiet hours. The patient then taps in the phone.</p>
        </Card>

        <Card n="3" title="Cancel the seeded appointment" hint="Shows a freed slot going to the waitlist.">
          {st?.cancel_me ? (
            <>
              <div className="rounded-lg bg-black/5 px-3 py-2 text-xs">
                <b>{st.cancel_me.patient_name}</b> · {fmtDay(istDate(st.cancel_me.start_at))}, {fmtTime(st.cancel_me.start_at)}<br />
                {st.cancel_me.next_waitlist_patient ? <>Next on the waitlist: <b>{st.cancel_me.next_waitlist_patient}</b></> : "No waitlist match"}
              </div>
              <button className={btnPrimary} disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? "Cancelling…" : "Cancel it & offer the slot"}</button>
            </>
          ) : <p className="rounded-lg bg-black/5 px-3 py-2 text-xs">The seeded appointment is already cancelled. Use “Reset demo” to restore it.</p>}
          <div className="mt-auto flex items-center gap-2 pt-1 text-xs">
            <span className="text-[var(--muted)]">Offer lasts</span>
            {[2, 15].map((m) => (
              <button key={m} aria-pressed={st?.waitlist_ttl_min === m} onClick={() => ttl.mutate(m)}
                className={`rounded-md border px-2.5 py-1 font-medium ${st?.waitlist_ttl_min === m ? "border-teal-600 bg-teal-600 text-white" : "border-[var(--line)] hover:bg-black/5"}`}>{m} min</button>
            ))}
            {st && ![2, 15].includes(st.waitlist_ttl_min) && <span className="text-[var(--muted)]">({st.waitlist_ttl_min} min)</span>}
          </div>
        </Card>

        <Card n="4" title="Mode, summary, reset">
          <div className="flex rounded-lg border border-[var(--line)] p-0.5 text-sm" role="group" aria-label="Messaging mode">
            {(["simulator", "live"] as const).map((m) => (
              <button key={m} aria-pressed={st?.mode === m} onClick={() => st?.mode !== m && mode.mutate(m)}
                className={`flex-1 rounded-md px-3 py-1.5 font-medium ${st?.mode === m ? "bg-teal-600 text-white" : "text-[var(--muted)]"}`}>{m === "live" ? "Live" : "Simulator"}</button>
            ))}
          </div>
          <p className="text-xs text-[var(--muted)]">{st && !st.live_ready ? "Live is not set up here: Simulator is the safe fallback if the network fails." : "Switch to Simulator in one click if WhatsApp is slow."}</p>
          <div className="flex gap-2">
            <select value={doctorId || doctors[0]?.id || ""} onChange={(e) => setDoctorId(e.target.value)} aria-label="Doctor" className={inputCls}>
              {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <button className={btnGhost} disabled={!doctors.length || summary.isPending} onClick={() => summary.mutate()}>Send 8 a.m. summary</button>
          </div>
          {confirmReset ? (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-2.5 text-xs" role="alertdialog" aria-label="Confirm reset">
              Wipe the demo data and re-seed for today?
              <div className="mt-2 flex gap-2">
                <button className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60" disabled={reset.isPending} onClick={() => reset.mutate()}>{reset.isPending ? "Resetting…" : "Yes, reset"}</button>
                <button className={`${btnGhost} !px-3 !py-1.5 text-xs`} onClick={() => setConfirmReset(false)}>Keep</button>
              </div>
            </div>
          ) : <button className={`${btnGhost} mt-auto`} onClick={() => setConfirmReset(true)}>Reset demo</button>}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[400px_minmax(0,1fr)]">
        <SimulatorPane compact focus={focus} />
        <section aria-label="Live schedule">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <button className={btnGhost} onClick={() => setDate(addDays(date, -1))} aria-label="Previous day">‹</button>
            <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} aria-label="Date" className="rounded-lg border border-[var(--line)] bg-transparent px-3 py-2 text-sm" />
            <button className={btnGhost} onClick={() => setDate(addDays(date, 1))} aria-label="Next day">›</button>
            <span className="ml-2 text-sm text-[var(--muted)]">{fmtDay(date)} · {counts.total} booked · <b className="text-[var(--ink)]">{counts.confirmed}</b> confirmed · {counts.pending} pending{counts.rescheduled ? ` · ${counts.rescheduled} rescheduled` : ""}{counts.cancelled ? ` · ${counts.cancelled} cancelled` : ""}</span>
          </div>
          {apptsQ.isError ? <p role="alert" className="rounded-lg bg-red-500/10 p-4 text-sm text-red-700">Could not load the schedule. Retrying…</p>
            : !appts || !ref.data ? <div className="h-72 animate-pulse rounded-xl bg-black/5" />
            : closed ? <Empty>Closed on this day.{nextWorking() && <div className="mt-3"><button className={btnPrimary} onClick={() => setDate(nextWorking()!)}>Go to {fmtDay(nextWorking()!)}</button></div>}</Empty>
            : <DayGrid date={date} doctors={doctors} appts={appts} nowMs={nowMs} highlight={highlight} selectedId={selected?.id}
                onOpen={(a) => setSelected({ id: a.id, label: `${a.patient_name} · ${fmtDay(istDate(a.start_at))} ${fmtTime(a.start_at)}` })} />}
          <p className="mt-2 text-xs text-[var(--muted)]">Click a card to select it for “Fire a reminder”. Cards change colour the moment the patient taps.</p>
        </section>
      </div>
    </div>
  );
}
