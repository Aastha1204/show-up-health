"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError, post } from "@/lib/api";
import { useLive } from "@/lib/live";
import { addDays, fmtDay, fmtTime, istDate } from "@/lib/time";
import type { Appt, Doctor, Reference, Slots } from "@/lib/types";
import PatientPicker, { emptyPatient, patientErrors, patientPayload, type PatientDraft } from "./PatientPicker";
import { useToast } from "./Toasts";
import { btnGhost, btnPrimary, Field, inputCls, Modal } from "./ui";

export type FormMode =
  | { mode: "new"; prefill?: { doctorId?: string; startIso?: string } }
  | { mode: "edit"; appt: Appt };

const ms = (iso: string) => new Date(iso).getTime();

export default function AppointmentForm({ state, onClose }: { state: FormMode; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const live = useLive();
  const edit = state.mode === "edit" ? state.appt : null;
  const pre = state.mode === "new" ? state.prefill : undefined;

  const ref = useQuery({ queryKey: ["reference"], queryFn: () => api<Reference>("/reference"), staleTime: 60_000 });
  const [patient, setPatient] = useState<PatientDraft>(emptyPatient());
  const [doctorId, setDoctorId] = useState(edit?.doctor_id ?? pre?.doctorId ?? "");
  const [treatmentId, setTreatmentId] = useState(edit?.treatment_type_id ?? "");
  const [date, setDate] = useState(edit ? istDate(edit.start_at) : pre?.startIso ? istDate(pre.startIso) : istDate());
  const [slot, setSlot] = useState<string | null>(edit?.start_at ?? pre?.startIso ?? null);
  const [note, setNote] = useState(edit?.internal_note ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => { if (ref.data && !doctorId) setDoctorId(ref.data.doctors[0]?.id ?? ""); }, [ref.data, doctorId]);
  useEffect(() => { if (ref.data && !treatmentId) setTreatmentId(ref.data.treatment_types.find((t) => t.name === "Consultation")?.id ?? ref.data.treatment_types[0]?.id ?? ""); }, [ref.data, treatmentId]);

  const treatment = ref.data?.treatment_types.find((t) => t.id === treatmentId);
  const slotsQ = useQuery({
    queryKey: ["slots", doctorId, date, treatmentId, edit?.id ?? null],
    enabled: !!doctorId && !!treatmentId,
    queryFn: () => api<Slots>(`/slots?doctor_id=${doctorId}&date=${date}&treatment_type_id=${treatmentId}${edit ? `&exclude_appointment_id=${edit.id}` : ""}`),
    refetchInterval: live.pollMs === false ? 15_000 : 8_000,
  });
  const slotList = slotsQ.data?.slots ?? [];
  const slotMs = useMemo(() => new Set(slotList.map(ms)), [slotList]);

  // a chosen time that stops being available (changed doctor/treatment/date, or someone just took it) is dropped
  useEffect(() => {
    if (slotsQ.data && slot && !slotMs.has(ms(slot))) setSlot(null);
  }, [slotsQ.data, slotMs, slot]);

  const save = useMutation({
    mutationFn: () => edit
      ? api<Appt>(`/appointments/${edit.id}`, { method: "PATCH", body: JSON.stringify({ doctor_id: doctorId, treatment_type_id: treatmentId, start_at: slot, internal_note: note || null }) })
      : post<Appt>("/appointments", { ...patientPayload(patient), doctor_id: doctorId, treatment_type_id: treatmentId, start_at: slot, internal_note: note || null }),
    onSuccess: (a) => {
      toast(`${edit ? "Moved to" : "Booked for"} ${fmtDay(istDate(a.start_at))}, ${fmtTime(a.start_at)}`, "success");
      qc.invalidateQueries();
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        setErr("That time was just taken by another booking. Pick another time below.");
        setSlot(null);
        slotsQ.refetch();
      } else setErr((e as Error).message);
    },
  });

  function submit(ev: React.FormEvent) {
    ev.preventDefault();
    setShowErrors(true);
    setErr(null);
    if (!edit && Object.keys(patientErrors(patient)).length) return;
    if (!slot) return setErr("Choose an available time.");
    save.mutate();
  }

  const today = istDate();
  return (
    <Modal title={edit ? `Reschedule · ${edit.patient_name}` : "Add appointment"} onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {edit ? (
          <div className="rounded-lg border border-[var(--line)] p-3 text-sm">
            <div className="font-semibold">{edit.patient_name}</div>
            <div className="text-xs text-[var(--muted)]">{edit.patient_phone} · currently {fmtDay(istDate(edit.start_at))}, {fmtTime(edit.start_at)} with {edit.doctor_name}</div>
          </div>
        ) : <PatientPicker value={patient} onChange={setPatient} showErrors={showErrors} />}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Doctor">
            <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} className={inputCls}>
              {ref.data?.doctors.map((d: Doctor) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          <Field label="Treatment" hint={treatment ? `${treatment.duration_min} minutes: the length of the slot` : undefined}>
            <select value={treatmentId} onChange={(e) => setTreatmentId(e.target.value)} className={inputCls}>
              {ref.data?.treatment_types.map((t) => <option key={t.id} value={t.id}>{t.name} · {t.duration_min} min</option>)}
            </select>
          </Field>
        </div>
        <p className="-mt-2 text-xs text-[var(--muted)]">Treatment names are for the clinic only; they are never sent to patients.</p>

        <div className="text-sm">
          <label htmlFor="appt-date" className="mb-1 block font-medium">Date</label>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setDate(addDays(date, -1))} disabled={date <= today} className={btnGhost} aria-label="Previous day">‹</button>
            <input id="appt-date" type="date" value={date} min={today} onChange={(e) => e.target.value && setDate(e.target.value)} className={inputCls} />
            <button type="button" onClick={() => setDate(addDays(date, 1))} className={btnGhost} aria-label="Next day">›</button>
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-baseline justify-between text-sm font-medium">
            <span>Available times · {fmtDay(date)}</span>
            {slotsQ.isFetching && <span className="text-xs font-normal text-[var(--muted)]">updating…</span>}
          </div>
          {slotsQ.isLoading ? <p className="text-sm text-[var(--muted)]">Loading times…</p>
            : slotList.length === 0 ? <p className="rounded-lg border border-dashed border-[var(--line)] p-3 text-sm text-[var(--muted)]">No free times on this day. Try another date or doctor.</p>
            : (
              <div className="grid max-h-48 grid-cols-3 gap-1.5 overflow-y-auto sm:grid-cols-5" role="radiogroup" aria-label="Available times">
                {slotList.map((s) => {
                  const on = slot !== null && ms(slot) === ms(s);
                  const current = !!edit && ms(edit.start_at) === ms(s);
                  return (
                    <button key={s} type="button" role="radio" aria-checked={on} data-slot={s} onClick={() => { setSlot(s); setErr(null); }}
                      className={`rounded-lg border px-2 py-1.5 text-sm transition-colors ${on ? "border-teal-600 bg-teal-600 font-semibold text-white" : "border-[var(--line)] hover:bg-black/5"}`}>
                      {fmtTime(s)}{current && <span className="block text-[10px] opacity-80">current</span>}
                    </button>
                  );
                })}
              </div>
            )}
        </div>

        <Field label="Internal note (optional)">
          <input value={note} onChange={(e) => setNote(e.target.value)} className={inputCls} maxLength={300} placeholder="Visible to staff only" />
        </Field>

        {err && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className={btnGhost}>Cancel</button>
          <button disabled={save.isPending} className={btnPrimary}>{save.isPending ? "Saving…" : edit ? "Move appointment" : "Book appointment"}</button>
        </div>
      </form>
    </Modal>
  );
}
