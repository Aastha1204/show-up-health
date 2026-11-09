"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import PatientPicker, { emptyPatient, patientErrors, patientPayload, type PatientDraft } from "@/components/PatientPicker";
import { useToast } from "@/components/Toasts";
import { btnGhost, btnPrimary, Countdown, Empty, Field, inputCls, Modal } from "@/components/ui";
import { api, ApiError, post } from "@/lib/api";
import { relTime, fmtDay, fmtDayLong, fmtTime, istDate } from "@/lib/time";
import { useLive } from "@/lib/live";
import type { Reference, WaitlistEntry } from "@/lib/types";

const WINDOWS = { morning: "Morning (before 2 pm)", afternoon: "Afternoon (2 – 5 pm)", evening: "Evening (after 5 pm)", any: "Any time" } as const;

function AddEntry({ ref, onClose }: { ref: Reference; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [patient, setPatient] = useState<PatientDraft>(emptyPatient());
  const [doctorId, setDoctorId] = useState("");
  const [treatmentId, setTreatmentId] = useState("");
  const [date, setDate] = useState(istDate());
  const [win, setWin] = useState<keyof typeof WINDOWS>("any");
  const [show, setShow] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const add = useMutation({
    mutationFn: () => post("/waitlist", { ...patientPayload(patient), doctor_id: doctorId || null, treatment_type_id: treatmentId || null, preferred_date: date, preferred_window: win }),
    onSuccess: () => { toast("Added to the waitlist", "success"); qc.invalidateQueries(); onClose(); },
    onError: (e) => setErr((e as Error).message),
  });
  return (
    <Modal title="Add to waitlist" onClose={onClose} wide>
      <form noValidate className="space-y-4" onSubmit={(e) => { e.preventDefault(); setShow(true); if (Object.keys(patientErrors(patient)).length) return; add.mutate(); }}>
        <PatientPicker value={patient} onChange={setPatient} showErrors={show} />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Doctor">
            <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} className={inputCls}>
              <option value="">Any doctor</option>
              {ref.doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="Treatment" hint="Decides how long a slot they need">
            <select value={treatmentId} onChange={(e) => setTreatmentId(e.target.value)} className={inputCls}>
              <option value="">Not specified</option>
              {ref.treatment_types.map((t) => <option key={t.id} value={t.id}>{t.name} · {t.duration_min} min</option>)}
            </select>
          </Field>
          <Field label="Wanted date"><input type="date" min={istDate()} value={date} onChange={(e) => e.target.value && setDate(e.target.value)} className={inputCls} /></Field>
          <Field label="Time of day">
            <select value={win} onChange={(e) => setWin(e.target.value as keyof typeof WINDOWS)} className={inputCls}>
              {Object.entries(WINDOWS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
        </div>
        {err && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{err}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className={btnGhost} onClick={onClose}>Cancel</button>
          <button className={btnPrimary} disabled={add.isPending}>{add.isPending ? "Adding…" : "Add to waitlist"}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function WaitlistPage() {
  const live = useLive();
  const qc = useQueryClient();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const ref = useQuery({ queryKey: ["reference"], queryFn: () => api<Reference>("/reference"), staleTime: 60_000 });
  const q = useQuery({ queryKey: ["waitlist"], queryFn: () => api<WaitlistEntry[]>("/waitlist"), refetchInterval: live.pollMs });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/waitlist/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast("Removed from the waitlist", "success"); setRemoving(null); qc.invalidateQueries(); },
    onError: (e) => toast(e instanceof ApiError ? e.message : "Could not remove", "error"),
  });

  const groups = useMemo(() => {
    const m = new Map<string, WaitlistEntry[]>();
    for (const e of q.data ?? []) m.set(e.preferred_date, [...(m.get(e.preferred_date) ?? []), e]);
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [q.data]);
  const doctorName = (id: string | null) => ref.data?.doctors.find((d) => d.id === id)?.name ?? "";

  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Waitlist</h1>
          <p className="text-xs text-[var(--muted)]">When a slot frees up it is offered to one person at a time, first come first served. They have {ref.data?.clinic.waitlist_offer_ttl_min ?? 15} minutes to answer.</p>
        </div>
        <button className={btnPrimary} onClick={() => setAdding(true)} disabled={!ref.data}>+ Add to waitlist</button>
      </div>

      {q.isLoading ? <div className="h-40 animate-pulse rounded-xl bg-black/5" />
        : groups.length === 0 ? <Empty>Nobody is waiting right now. Patients can be added here, and they are offered any matching slot that opens up.</Empty>
        : groups.map(([day, entries]) => (
          <section key={day} className="mb-6" aria-label={fmtDayLong(day)}>
            <h2 className="mb-2 text-sm font-semibold text-[var(--muted)]">{fmtDay(day)}{day === istDate() && " · today"}</h2>
            <ul className="space-y-2">
              {entries.map((e, i) => (
                <li key={e.id} data-entry={e.patient_name} className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-3.5">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="font-semibold">{e.patient_name} <span className="text-xs font-normal text-[var(--muted)]">{e.patient_phone}</span></div>
                      <div className="mt-0.5 text-xs text-[var(--muted)]">
                        {e.doctor_name ?? "Any doctor"} · {WINDOWS[e.preferred_window]}{e.treatment_name ? ` · ${e.treatment_name}` : ""} · added {relTime(e.created_at)}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[var(--muted)]">#{i + 1} in line</span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${e.status === "offered" ? "bg-sky-500/15 text-sky-700 dark:text-sky-300" : "bg-black/5 text-[var(--muted)]"}`}>
                        {e.status === "offered" ? "Offer sent" : "Waiting"}
                      </span>
                    </div>
                  </div>

                  {e.status === "offered" && e.offer_expires_at && e.offer_slot_start_at && (
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-sky-500/10 px-3 py-2 text-sm" role="status">
                      <span>Offered <b>{fmtTime(e.offer_slot_start_at)}</b> with {doctorName(e.offer_doctor_id)}</span>
                      <span>Expires in <Countdown to={e.offer_expires_at} className="font-semibold" /></span>
                    </div>
                  )}

                  <div className="mt-3 flex items-center gap-2">
                    {removing === e.id ? (
                      <>
                        <span className="text-xs text-[var(--muted)]">{e.status === "offered" ? "Remove them and pass the offer on?" : "Remove from the waitlist?"}</span>
                        <button className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60" disabled={remove.isPending} onClick={() => remove.mutate(e.id)}>Yes, remove</button>
                        <button className={`${btnGhost} !px-3 !py-1.5 text-xs`} onClick={() => setRemoving(null)}>Keep</button>
                      </>
                    ) : <button className={`${btnGhost} !px-3 !py-1.5 text-xs`} onClick={() => setRemoving(e.id)}>Remove</button>}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
      {adding && ref.data && <AddEntry ref={ref.data} onClose={() => setAdding(false)} />}
    </div>
  );
}
