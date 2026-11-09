"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { tenDigits } from "@/components/PatientPicker";
import { addDays, fmtDay, fmtDayLong, fmtTime, istDate } from "@/lib/time";
import { btnPrimary, Field, inputCls } from "@/components/ui";

type Clinic = { name: string; address: string | null; consent_text: string; max_days_ahead: number;
  doctors: { id: string; name: string }[]; treatments: { id: string; name: string; duration_min: number }[] };
type Done = { start_at: string; doctor_name: string; clinic_name: string };

/** Public calls: never send the staff token. The server also checks Origin, rate-limits, and ignores the honeypot. */
async function pub<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/public${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  if (!res.ok) {
    let msg = "Something went wrong. Please try again.";
    try {
      const j = await res.json();
      const d = j.detail;
      msg = Array.isArray(d) ? String(d[0]?.msg ?? msg).replace(/^Value error, /, "") : d?.message ?? msg;
    } catch { /* not json */ }
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return res.json();
}

export default function BookingForm({ embedded = false }: { embedded?: boolean }) {
  const clinicQ = useQuery({ queryKey: ["public-clinic"], queryFn: () => pub<Clinic>("/clinic"), staleTime: 300_000 });
  const clinic = clinicQ.data;
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [language, setLanguage] = useState("en");
  const [treatmentId, setTreatmentId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [date, setDate] = useState(() => addDays(istDate(), 1));
  const [slot, setSlot] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [website, setWebsite] = useState("");                // honeypot: invisible to people
  const [err, setErr] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);

  useEffect(() => { if (clinic && !treatmentId) setTreatmentId((clinic.treatments.find((t) => t.name === "Consultation") ?? clinic.treatments[0])?.id ?? ""); }, [clinic, treatmentId]);

  const slotsQ = useQuery({
    queryKey: ["public-slots", doctorId, date, treatmentId], enabled: !!treatmentId,
    queryFn: () => pub<{ slots: string[] }>(`/slots?date=${date}&treatment_type_id=${treatmentId}${doctorId ? `&doctor_id=${doctorId}` : ""}`),
  });
  const slots = slotsQ.data?.slots ?? [];
  const slotMs = useMemo(() => new Set(slots.map((s) => new Date(s).getTime())), [slots]);
  useEffect(() => { if (slotsQ.data && slot && !slotMs.has(new Date(slot).getTime())) setSlot(null); }, [slotsQ.data, slotMs, slot]);

  const phoneOk = /^[6-9]\d{9}$/.test(tenDigits(phone));
  const problems = { name: !name.trim() ? "Please enter your name" : null, phone: !phoneOk ? "Enter a 10-digit mobile number starting 6-9" : null,
    slot: !slot ? "Please choose a time" : null, consent: !consent ? `Please tick the box: “${clinic?.consent_text ?? ""}”` : null };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setShowErrors(true);
    setErr(null);
    if (Object.values(problems).some(Boolean)) return;
    setBusy(true);
    try {
      const r = await pub<Done>("/appointments", { method: "POST", body: JSON.stringify({
        name: name.trim(), phone: "+91" + tenDigits(phone), language, doctor_id: doctorId || null, treatment_type_id: treatmentId,
        start_at: slot, consent, website }) });
      setDone(r);
    } catch (ex) {
      setErr((ex as Error).message);
      if ((ex as { status?: number }).status === 409) { setSlot(null); slotsQ.refetch(); }
    } finally { setBusy(false); }
  }

  if (clinicQ.isError) return <p role="alert" className="rounded-lg bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">Booking is not available right now. Please call the clinic.</p>;
  if (!clinic) return <div className="h-64 animate-pulse rounded-xl bg-black/5" aria-label="Loading" />;

  if (done) {
    const d = istDate(done.start_at);
    return (
      <div role="status" data-testid="booking-success" className="rounded-2xl border border-teal-600/40 bg-teal-600/10 p-6 text-center">
        <div className="text-3xl" aria-hidden>✅</div>
        <h2 className="mt-2 text-lg font-semibold">You&apos;re booked</h2>
        <p className="mt-1 text-sm">{fmtDayLong(d)} at <b>{fmtTime(done.start_at)}</b> with {done.doctor_name} at {done.clinic_name}.</p>
        <p className="mt-3 text-sm text-[var(--muted)]">We&apos;ll send a confirmation on WhatsApp shortly. You can confirm, change or cancel from the same chat.</p>
      </div>
    );
  }

  const max = addDays(istDate(), clinic.max_days_ahead);
  return (
    <form onSubmit={submit} noValidate className="space-y-4" aria-label="Book an appointment">
      {/* honeypot: people never see or reach this; bots fill it in. Not display:none, which some bots skip. */}
      <div aria-hidden="true" style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}>
        <label>Website <input name="website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} /></label>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Your name" error={showErrors ? problems.name : null}><input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} autoComplete="name" /></Field>
        <Field label="Mobile number (WhatsApp)" error={showErrors ? problems.phone : null}>
          <div className="flex"><span className="grid place-items-center rounded-l-lg border border-r-0 border-[var(--line)] px-3 text-sm text-[var(--muted)]">+91</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel" autoComplete="tel-national" placeholder="98765 43210" className={`${inputCls} rounded-l-none`} /></div>
        </Field>
        <Field label="Service">
          <select value={treatmentId} onChange={(e) => setTreatmentId(e.target.value)} className={inputCls}>{clinic.treatments.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
        </Field>
        <Field label="Doctor">
          <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} className={inputCls}>
            <option value="">No preference</option>{clinic.doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        <Field label="Preferred language for messages">
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className={inputCls}><option value="en">English</option><option value="hi">हिन्दी (Hindi)</option><option value="mr">मराठी (Marathi)</option></select>
        </Field>
        <div className="text-sm">
          <label htmlFor="book-date" className="mb-1 block font-medium">Date</label>
          <input id="book-date" type="date" value={date} min={istDate()} max={max} onChange={(e) => e.target.value && setDate(e.target.value)} className={inputCls} />
        </div>
      </div>

      <div>
        <div className="mb-1 text-sm font-medium">Available times · {fmtDay(date)}</div>
        {slotsQ.isLoading ? <p className="text-sm text-[var(--muted)]">Loading times…</p>
          : slots.length === 0 ? <p className="rounded-lg border border-dashed border-[var(--line)] p-3 text-sm text-[var(--muted)]">No times left on this day. Please try another date.</p>
          : <div className="grid max-h-44 grid-cols-3 gap-1.5 overflow-y-auto sm:grid-cols-5" role="radiogroup" aria-label="Available times">
              {slots.map((s) => { const on = slot !== null && new Date(slot).getTime() === new Date(s).getTime(); return (
                <button key={s} type="button" role="radio" aria-checked={on} data-slot={s} onClick={() => { setSlot(s); setErr(null); }}
                  className={`rounded-lg border px-2 py-1.5 text-sm ${on ? "border-teal-600 bg-teal-600 font-semibold text-white" : "border-[var(--line)] hover:bg-black/5"}`}>{fmtTime(s)}</button>); })}
            </div>}
        {showErrors && problems.slot && <p role="alert" className="mt-1 text-xs text-red-600">{problems.slot}</p>}
      </div>

      <div>
        <label className="flex items-start gap-2.5 text-sm">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 accent-teal-600" />
          <span>{clinic.consent_text}</span>
        </label>
        {showErrors && problems.consent && <p role="alert" className="mt-1 text-xs text-red-600">{problems.consent}</p>}
        <p className="mt-1 pl-6 text-xs text-[var(--muted)]">Messages are reminders about your appointment only. Reply STOP any time to stop them.</p>
      </div>

      {err && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{err}</p>}
      <button disabled={busy} className={`${btnPrimary} w-full py-2.5`}>{busy ? "Booking…" : "Book appointment"}</button>
    </form>
  );
}
