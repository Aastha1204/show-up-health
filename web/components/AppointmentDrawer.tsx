"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { fmtDayLong, fmtTime, istDate } from "@/lib/time";
import { LANGUAGE_LABEL, SOURCE_ICON, STATUS_LABEL, type Appt, type Status } from "@/lib/types";
import MessageLog from "./MessageLog";
import { useToast } from "./Toasts";
import { btnGhost, StatusChip } from "./ui";

const UPCOMING: Status[] = ["pending", "confirmed", "rescheduled"];

export default function AppointmentDrawer({ appt, nowMs, onClose, onReschedule }: { appt: Appt; nowMs: number; onClose: () => void; onReschedule: (a: Appt) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<"details" | "log">("details");
  const [confirmCancel, setConfirmCancel] = useState(false);
  useEffect(() => { setTab("details"); setConfirmCancel(false); }, [appt.id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const change = useMutation({
    mutationFn: (status: Status) => post(`/appointments/${appt.id}/status`, { status }),
    onSuccess: (_d, status) => { toast(`${appt.patient_name}: ${STATUS_LABEL[status].toLowerCase()}`, "success"); setConfirmCancel(false); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "error"),
  });

  const deposit = useMutation({
    mutationFn: () => post<{ sent: boolean; not_sent_reason: string | null }>(`/appointments/${appt.id}/deposit`, {}),
    onSuccess: (r) => { toast(r.sent ? `Deposit link sent to ${appt.patient_name}` : `Link created but not sent (${r.not_sent_reason}). Copy it from the drawer.`, r.sent ? "success" : "error"); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const simPaid = useMutation({
    mutationFn: () => post(`/demo/deposit/${appt.id}/pay`),
    onSuccess: () => { toast("Simulated payment received", "success"); qc.invalidateQueries(); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const upcoming = UPCOMING.includes(appt.status);
  const simulatedLink = !!appt.payment_link_url && appt.payment_link_url.includes("/demo-");
  const started = nowMs >= new Date(appt.start_at).getTime();

  return (
    <aside className="drawer-in fixed inset-y-0 right-0 z-40 flex w-full max-w-[400px] flex-col border-l border-[var(--line)] bg-[var(--surface)] shadow-2xl" aria-label={`Appointment for ${appt.patient_name}`}>
      <div className="flex items-start justify-between gap-2 border-b border-[var(--line)] p-4">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{appt.patient_name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
            <StatusChip status={appt.status} />
            <span className="text-[var(--muted)]">{fmtTime(appt.start_at)} – {fmtTime(appt.end_at)}</span>
          </div>
        </div>
        <button onClick={onClose} aria-label="Close" className="rounded-md px-2 text-2xl leading-none text-[var(--muted)] hover:bg-black/5">×</button>
      </div>

      <div className="flex border-b border-[var(--line)] text-sm" role="tablist">
        {(["details", "log"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`flex-1 border-b-2 py-2.5 font-medium ${tab === t ? "border-teal-600 text-teal-700 dark:text-teal-300" : "border-transparent text-[var(--muted)]"}`}>
            {t === "details" ? "Details" : "Message log"}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {tab === "details" ? (
          <div className="space-y-4 text-sm">
            <dl className="grid grid-cols-[110px_1fr] gap-y-2">
              <dt className="text-[var(--muted)]">Date</dt><dd>{fmtDayLong(istDate(appt.start_at))}</dd>
              <dt className="text-[var(--muted)]">Doctor</dt><dd>{appt.doctor_name}</dd>
              <dt className="text-[var(--muted)]">Treatment</dt><dd>{appt.treatment_name ?? "—"} · {appt.duration_min} min <span className="text-xs text-[var(--muted)]">(internal)</span></dd>
              <dt className="text-[var(--muted)]">Phone</dt><dd><a className="text-teal-700 underline dark:text-teal-300" href={`tel:${appt.patient_phone}`}>{appt.patient_phone}</a></dd>
              <dt className="text-[var(--muted)]">Language</dt><dd>{LANGUAGE_LABEL[appt.patient_language]}</dd>
              <dt className="text-[var(--muted)]">Source</dt><dd>{SOURCE_ICON[appt.source].icon} {SOURCE_ICON[appt.source].label}{appt.recovered_via_waitlist && " · recovered slot"}</dd>
              {appt.deposit_status && <><dt className="text-[var(--muted)]">Deposit</dt><dd data-testid="deposit-status">
                {appt.deposit_status === "paid" ? <span className="font-medium text-emerald-700 dark:text-emerald-300">✓ ₹{appt.deposit_amount} paid</span>
                  : appt.deposit_status === "expired" ? <span className="text-amber-700 dark:text-amber-300">₹{appt.deposit_amount} link expired</span>
                  : <span>₹{appt.deposit_amount} requested · waiting {appt.payment_link_url && <a className="text-teal-700 underline dark:text-teal-300" href={appt.payment_link_url} target="_blank" rel="noreferrer">link</a>}</span>}
              </dd></>}
              {appt.internal_note && <><dt className="text-[var(--muted)]">Note</dt><dd>{appt.internal_note}</dd></>}
            </dl>
            {appt.patient_opted_out && <p className="rounded-lg bg-amber-500/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">This patient opted out of WhatsApp: they receive no reminders. Phone them.</p>}
            {appt.patient_no_show_count >= 2 && <p className="rounded-lg bg-red-500/15 px-3 py-2 text-xs text-red-800 dark:text-red-200">⚠ {appt.patient_no_show_count} previous no-shows</p>}

            <div className="flex flex-wrap gap-2 border-t border-[var(--line)] pt-4">
              <button className={btnGhost} disabled={!upcoming || change.isPending} onClick={() => change.mutate("arrived")}>Mark arrived</button>
              <button className={btnGhost} disabled={!upcoming || !started || change.isPending} onClick={() => change.mutate("no_show")}
                title={!started && upcoming ? "Available once the appointment time has started" : undefined}>No-show</button>
              <button className={btnGhost} disabled={!upcoming} onClick={() => onReschedule(appt)}>Reschedule</button>
              <button className={`${btnGhost} text-red-700 dark:text-red-300`} disabled={!upcoming || change.isPending} onClick={() => setConfirmCancel(true)}>Cancel appointment</button>
              <button className={btnGhost} onClick={() => setTab("log")}>Message log</button>
              {appt.deposit_status !== "paid" && <button className={btnGhost} disabled={!upcoming || deposit.isPending} onClick={() => deposit.mutate()}>
                {appt.deposit_status === "requested" || appt.deposit_status === "expired" ? "Send deposit link again" : "Request deposit (₹500)"}</button>}
              {appt.deposit_status === "requested" && simulatedLink && <button className={btnGhost} disabled={simPaid.isPending} onClick={() => simPaid.mutate()}>Simulate: patient paid</button>}
            </div>
            {!upcoming && <p className="text-xs text-[var(--muted)]">This appointment is {STATUS_LABEL[appt.status].toLowerCase()}, so it can no longer be changed.</p>}

            {confirmCancel && (
              <div role="alertdialog" aria-label="Confirm cancellation" className="rounded-lg border border-red-500/40 bg-red-500/10 p-3">
                <p className="text-sm font-medium">Cancel {appt.patient_name}&apos;s appointment?</p>
                <p className="mt-1 text-xs text-[var(--muted)]">The slot is offered to the next person on the waitlist, if there is one.</p>
                <div className="mt-3 flex gap-2">
                  <button className="rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60" disabled={change.isPending} onClick={() => change.mutate("cancelled")}>Yes, cancel it</button>
                  <button className={btnGhost} onClick={() => setConfirmCancel(false)}>Keep it</button>
                </div>
              </div>
            )}
          </div>
        ) : <MessageLog appointmentId={appt.id} />}
      </div>
    </aside>
  );
}
