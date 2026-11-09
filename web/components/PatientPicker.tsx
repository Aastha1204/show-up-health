"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useDebounced } from "@/lib/hooks";
import { CONSENT_LABEL, LANGUAGE_LABEL, type ConsentSource, type Language, type Patient } from "@/lib/types";
import { Field, inputCls } from "./ui";

export type PatientDraft = {
  mode: "search" | "new";
  query: string;
  patient: Patient | null;
  name: string;
  phone: string;                 // what the user typed after "+91"
  language: Language;
  consent: ConsentSource | "";
};
export const emptyPatient = (): PatientDraft => ({ mode: "search", query: "", patient: null, name: "", phone: "", language: "en", consent: "" });

/** Accepts "98765 43210", "+91 98765-43210", "09876543210", "919876543210" -> "9876543210" (10 digits) */
export function tenDigits(raw: string): string {
  let s = raw.replace(/[\s\-().]/g, "");
  if (s.startsWith("+")) s = s.slice(1);
  if (s.length === 12 && s.startsWith("91")) s = s.slice(2);
  if (s.length === 11 && s.startsWith("0")) s = s.slice(1);
  return s;
}
const MOBILE = /^[6-9]\d{9}$/;

export function patientErrors(d: PatientDraft): Partial<Record<"patient" | "name" | "phone" | "consent", string>> {
  const e: ReturnType<typeof patientErrors> = {};
  if (d.mode === "search") { if (!d.patient) e.patient = "Choose a patient, or add a new one"; return e; }
  if (!d.name.trim()) e.name = "Enter the patient's name";
  if (!MOBILE.test(tenDigits(d.phone))) e.phone = "Enter a 10-digit Indian mobile number starting 6-9";
  if (!d.consent) e.consent = "Required: how did the patient agree to WhatsApp updates?";
  return e;
}

export function patientPayload(d: PatientDraft) {
  return d.mode === "search"
    ? { patient_id: d.patient!.id }
    : { new_patient: { name: d.name.trim(), phone: "+91" + tenDigits(d.phone), language: d.language, consent_source: d.consent } };
}

export default function PatientPicker({ value, onChange, showErrors }: { value: PatientDraft; onChange: (d: PatientDraft) => void; showErrors: boolean }) {
  const q = useDebounced(value.query.trim(), 250);
  const found = useQuery({
    queryKey: ["patient-search", q], enabled: value.mode === "search" && !value.patient && q.length >= 2,
    queryFn: () => api<Patient[]>(`/patients?q=${encodeURIComponent(q)}&limit=8`),
  });
  const err = patientErrors(value);
  const set = (p: Partial<PatientDraft>) => onChange({ ...value, ...p });

  if (value.mode === "search") {
    if (value.patient) {
      const p = value.patient;
      return (
        <div className="rounded-lg border border-[var(--line)] p-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="font-semibold">{p.name}</div>
              <div className="text-xs text-[var(--muted)]">{p.phone} · {LANGUAGE_LABEL[p.language]}</div>
            </div>
            <button type="button" onClick={() => set({ patient: null, query: "" })} className="text-xs text-teal-700 underline dark:text-teal-300">Change</button>
          </div>
          {p.opted_out && <p className="mt-2 rounded bg-amber-500/15 px-2 py-1 text-xs text-amber-800 dark:text-amber-200">Opted out of WhatsApp: no reminders will be sent to this patient.</p>}
          {p.no_show_count >= 2 && <p className="mt-2 rounded bg-red-500/15 px-2 py-1 text-xs text-red-800 dark:text-red-200">{p.no_show_count} previous no-shows</p>}
        </div>
      );
    }
    return (
      <div>
        <Field label="Patient" error={showErrors ? err.patient : null}>
          <input value={value.query} onChange={(e) => set({ query: e.target.value })} placeholder="Search by name or phone…" className={inputCls} autoComplete="off" />
        </Field>
        {q.length >= 2 && (
          <ul className="mt-1 max-h-44 overflow-y-auto rounded-lg border border-[var(--line)]" aria-label="Matching patients">
            {found.isLoading && <li className="px-3 py-2 text-xs text-[var(--muted)]">Searching…</li>}
            {found.data?.map((p) => (
              <li key={p.id}>
                <button type="button" onClick={() => set({ patient: p })} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-black/5">
                  <span>{p.name}</span><span className="text-xs text-[var(--muted)]">{p.phone}</span>
                </button>
              </li>
            ))}
            {found.data && found.data.length === 0 && <li className="px-3 py-2 text-xs text-[var(--muted)]">No match.</li>}
          </ul>
        )}
        <button type="button" onClick={() => set({ mode: "new", name: value.query })} className="mt-2 text-sm font-medium text-teal-700 underline dark:text-teal-300">+ Add a new patient</button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-[var(--line)] p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">New patient</span>
        <button type="button" onClick={() => set({ mode: "search" })} className="text-xs text-teal-700 underline dark:text-teal-300">Search instead</button>
      </div>
      <Field label="Name" error={showErrors ? err.name : null}>
        <input value={value.name} onChange={(e) => set({ name: e.target.value })} className={inputCls} autoComplete="off" />
      </Field>
      <Field label="Mobile number" error={showErrors ? err.phone : null}>
        <div className="flex">
          <span className="grid place-items-center rounded-l-lg border border-r-0 border-[var(--line)] px-3 text-sm text-[var(--muted)]">+91</span>
          <input value={value.phone} onChange={(e) => set({ phone: e.target.value })} inputMode="tel" placeholder="98765 43210" className={`${inputCls} rounded-l-none`} autoComplete="off" />
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Language">
          <select value={value.language} onChange={(e) => set({ language: e.target.value as Language })} className={inputCls}>
            {Object.entries(LANGUAGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="Consent given via" error={showErrors ? err.consent : null}>
          <select value={value.consent} onChange={(e) => set({ consent: e.target.value as ConsentSource })} className={inputCls}>
            <option value="">Select…</option>
            {Object.entries(CONSENT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
      </div>
      <p className="text-xs text-[var(--muted)]">The patient must have agreed to receive WhatsApp appointment updates. An existing number is matched, not duplicated.</p>
    </div>
  );
}
