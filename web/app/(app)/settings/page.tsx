"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useToast } from "@/components/Toasts";
import { btnGhost, btnPrimary, Field, inputCls } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { AuthGate } from "@/lib/auth";
import { relTime } from "@/lib/time";

type Hours = Record<string, [string, string][]>;
type Doctor = { id: string; name: string; whatsapp_phone: string | null; colour: string; working_hours: Hours; active: boolean; upcoming_appointments: number; is_demo_seed: boolean; calendar_token?: string };
type Treatment = { id: string; name: string; duration_min: number; recall_after_days: number | null; avg_ticket_value: number };
type Clinic = { name: string; address: string | null; maps_link: string | null; parking_note: string | null; phone: string | null; review_link: string | null; avg_ticket_value: number;
  baseline_no_show_rate: number; go_live_date: string | null; waitlist_offer_ttl_min: number; quiet_hours_start: string; quiet_hours_end: string; messaging_mode: "simulator" | "live";
  reminder_settings: Record<string, { enabled?: boolean; offset_min?: number; at?: string }> };
type SettingsData = { clinic: Clinic; doctors: Doctor[]; treatment_types: Treatment[] };
type Health = { mode: string; config: Record<string, boolean>; token: { checked: boolean; valid: boolean | null; error: string | null };
  phone: { display_phone_number?: string; verified_name?: string; quality_rating?: string } | null; templates: { name: string; status: string }[] | null;
  webhook: { last_received_at: string | null; age_seconds: number | null }; blockers: string[]; warnings: string[]; ready: boolean; webhook_url: string | null; webhook_path: string; api_version: string };
type Preview = { name: string; category: string; category_note: string | null; recipient: string; language: string; fallback: boolean; body: string; footer: string | null; buttons: string[] };

const DAYS: [string, string][] = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];
const REMINDERS: { key: string; label: string; offset?: string }[] = [
  { key: "confirmation", label: "Booking confirmation (sent straight away)" }, { key: "day_before", label: "Reminder the day before", offset: "minutes before the visit" },
  { key: "two_hour", label: "Reminder shortly before", offset: "minutes before the visit" }, { key: "post_visit", label: "Thank-you after the visit", offset: "minutes after it ends" },
  { key: "recall", label: "Check-up recall (days set per treatment)" }, { key: "daily_summary", label: "Doctor's morning summary" },
];

function Card({ title, hint, children, id }: { title: string; hint?: string; children: React.ReactNode; id?: string }) {
  return (
    <section id={id} className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-5" aria-label={title}>
      <h2 className="text-base font-semibold">{title}</h2>
      {hint && <p className="mt-0.5 text-xs text-[var(--muted)]">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function useSave<T>(path: string, method: "PATCH" | "POST" | "PUT", ok: string, after?: (r: T) => void) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: unknown) => api<T>(path, { method, body: JSON.stringify(body) }),
    onSuccess: (r) => { toast(ok, "success"); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["reference"] }); after?.(r); },
    onError: (e) => toast(e instanceof ApiError ? e.message : (e as Error).message, "error"),
  });
}

// ───────────────────────── WhatsApp ─────────────────────────
function WhatsApp() {
  const toast = useToast();
  const qc = useQueryClient();
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const h = useQuery({ queryKey: ["settings", "health"], queryFn: () => api<Health>("/settings/health"), refetchInterval: 30_000 });
  const recheck = useMutation({ mutationFn: () => api<Health>("/settings/health?refresh=true"), onSuccess: (d) => qc.setQueryData(["settings", "health"], d) });
  const mode = useMutation({
    mutationFn: (m: "live" | "simulator") => api("/settings/messaging-mode", { method: "PUT", body: JSON.stringify({ mode: m }) }),
    onSuccess: (_d, m) => { toast(m === "live" ? "Live WhatsApp is on: patients now receive real messages." : "Simulator mode: no real messages are sent.", "success"); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["demo-state"] }); },
    onError: (e) => toast(e instanceof ApiError ? e.message : (e as Error).message, "error"),
  });
  const submit = useMutation({
    mutationFn: () => api<{ results: { name: string; result: string; detail: string }[] }>("/settings/templates/submit", { method: "POST", body: JSON.stringify({ language: "en" }) }),
    onSuccess: (r) => { setConfirmSubmit(false); const c = r.results.reduce<Record<string, number>>((a, x) => ({ ...a, [x.result]: (a[x.result] ?? 0) + 1 }), {});
      toast(`Templates: ${Object.entries(c).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}`, r.results.some((x) => x.result === "error") ? "error" : "success"); recheck.mutate(); },
    onError: (e) => toast(e instanceof ApiError ? e.message : (e as Error).message, "error"),
  });
  const d = h.data;
  const Row = ({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) => (
    <li className="flex items-start gap-2 text-sm"><span aria-hidden className={ok === null ? "text-[var(--muted)]" : ok ? "text-emerald-600" : "text-red-600"}>{ok === null ? "•" : ok ? "✓" : "✕"}</span>
      <span><b className="font-medium">{label}</b>{detail && <span className="text-[var(--muted)]"> · {detail}</span>}</span></li>);
  return (
    <Card id="whatsapp" title="WhatsApp" hint="Live sends real messages through the WhatsApp Cloud API. Simulator never sends anything outside the app.">
      {!d ? <div className="h-24 animate-pulse rounded-lg bg-black/5" /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex rounded-lg border border-[var(--line)] p-0.5 text-sm" role="group" aria-label="Messaging mode">
              {(["simulator", "live"] as const).map((m) => (
                <button key={m} aria-pressed={d.mode === m} disabled={mode.isPending} onClick={() => d.mode !== m && mode.mutate(m)}
                  className={`rounded-md px-4 py-1.5 font-medium ${d.mode === m ? "bg-teal-600 text-white" : "text-[var(--muted)]"}`}>{m === "live" ? "Live" : "Simulator"}</button>))}
            </div>
            <button className={btnGhost} onClick={() => recheck.mutate()} disabled={recheck.isPending}>{recheck.isPending ? "Checking…" : "Re-check"}</button>
            <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${d.ready ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-red-500/15 text-red-700 dark:text-red-300"}`}>{d.ready ? "Ready for Live" : "Not ready for Live"}</span>
          </div>
          <ul className="space-y-1.5" aria-label="Health checks">
            <Row ok={d.config.access_token && d.config.phone_number_id} label="Access token and phone number ID are set" />
            <Row ok={d.token.checked ? d.token.valid : null} label="Meta accepts the token" detail={d.token.error ?? (d.token.valid ? undefined : "not checked yet")} />
            {d.phone && <Row ok label="WhatsApp number" detail={`${d.phone.display_phone_number ?? ""} · ${d.phone.verified_name ?? ""} · quality ${d.phone.quality_rating ?? "n/a"}`} />}
            <Row ok={d.config.app_secret} label="App secret set (verifies webhook signatures)" />
            <Row ok={d.config.verify_token} label="Webhook verify token set" />
            <Row ok={d.webhook.last_received_at ? true : null} label="Last webhook received" detail={d.webhook.last_received_at ? relTime(d.webhook.last_received_at) : "never"} />
          </ul>
          <p className="text-xs text-[var(--muted)]">Callback URL for Meta: <code className="rounded bg-black/5 px-1">{d.webhook_url ?? `https://<your-api-host>${d.webhook_path}`}</code> · subscribe to “messages”. Graph API {d.api_version}.</p>
          {d.blockers.map((b) => <p key={b} role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{b}</p>)}
          {d.warnings.map((w) => <p key={w} className="rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">{w}</p>)}
          {d.templates && (
            <div>
              <h3 className="mb-1 text-sm font-semibold">Template approval (Meta)</h3>
              <ul className="grid gap-1 text-sm sm:grid-cols-2">{d.templates.map((t) => (
                <li key={t.name} className="flex items-center justify-between rounded-lg border border-[var(--line)] px-3 py-1.5"><span>{t.name.replace("showup_", "")}</span>
                  <span className={`text-xs font-bold ${t.status === "APPROVED" ? "text-emerald-600" : t.status === "NOT_SUBMITTED" ? "text-[var(--muted)]" : "text-amber-600"}`}>{t.status.replace("_", " ")}</span></li>))}</ul>
            </div>)}
          {d.config.business_account_id && d.config.access_token && (
            confirmSubmit ? (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">Send the English templates to Meta for approval? They then cannot be edited freely.
                <div className="mt-2 flex gap-2"><button className={btnPrimary} onClick={() => submit.mutate()} disabled={submit.isPending}>{submit.isPending ? "Submitting…" : "Yes, submit"}</button><button className={btnGhost} onClick={() => setConfirmSubmit(false)}>Cancel</button></div></div>
            ) : <button className={btnGhost} onClick={() => setConfirmSubmit(true)}>Submit templates to Meta…</button>)}
        </div>)}
    </Card>);
}

// ───────────────────────── clinic, reminders ─────────────────────────
const clinicForm = (c: Clinic) => ({ name: c.name, address: c.address ?? "", maps_link: c.maps_link ?? "", parking_note: c.parking_note ?? "", phone: c.phone ?? "", review_link: c.review_link ?? "",
  avg_ticket_value: String(c.avg_ticket_value), baseline: String(+(c.baseline_no_show_rate * 100).toFixed(1)), go_live_date: c.go_live_date ?? "", ttl: String(c.waitlist_offer_ttl_min) });

function ClinicCard({ c }: { c: Clinic }) {
  // The form owns its state. It is NOT remounted when the page refetches (that would throw away what you are typing);
  // after a save it adopts the server's normalised values (trimmed name, +91 phone...) from the response.
  const [f, setF] = useState(() => clinicForm(c));
  const save = useSave<Clinic>("/settings/clinic", "PATCH", "Clinic details saved", (r) => setF(clinicForm(r)));
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <Card title="Clinic details" hint="The name, address and parking note appear in messages to patients, so they cannot mention treatments or medical terms.">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ name: f.name, address: f.address, maps_link: f.maps_link, parking_note: f.parking_note, phone: f.phone, review_link: f.review_link,
        avg_ticket_value: Number(f.avg_ticket_value), baseline_no_show_rate: Number(f.baseline) / 100, go_live_date: f.go_live_date || null, waitlist_offer_ttl_min: Number(f.ttl) }); }}>
        <Field label="Clinic name"><input className={inputCls} value={f.name} onChange={set("name")} /></Field>
        <Field label="Phone"><input className={inputCls} value={f.phone} onChange={set("phone")} placeholder="+91…" /></Field>
        <div className="sm:col-span-2"><Field label="Address"><input className={inputCls} value={f.address} onChange={set("address")} /></Field></div>
        <Field label="Maps link"><input className={inputCls} value={f.maps_link} onChange={set("maps_link")} placeholder="https://maps.google.com/…" /></Field>
        <Field label="Parking note"><input className={inputCls} value={f.parking_note} onChange={set("parking_note")} /></Field>
        <Field label="Review link (thank-you message)"><input className={inputCls} value={f.review_link} onChange={set("review_link")} placeholder="https://g.page/r/…" /></Field>
        <Field label="Average ticket (₹)" hint="Used for revenue saved on Analytics"><input type="number" min={0} className={inputCls} value={f.avg_ticket_value} onChange={set("avg_ticket_value")} /></Field>
        <Field label="Baseline no-show rate (%)" hint="Your rate before reminders"><input type="number" min={0} max={100} step="0.1" className={inputCls} value={f.baseline} onChange={set("baseline")} /></Field>
        <Field label="Reminders went live on"><input type="date" className={inputCls} value={f.go_live_date} onChange={set("go_live_date")} /></Field>
        <Field label="Waitlist offer lasts (minutes)"><input type="number" min={1} max={120} className={inputCls} value={f.ttl} onChange={set("ttl")} /></Field>
        <div className="sm:col-span-2"><button className={btnPrimary} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save clinic details"}</button></div>
      </form>
    </Card>);
}

function RemindersCard({ c }: { c: Clinic }) {
  const [rs, setRs] = useState(c.reminder_settings);
  const [qs, setQs] = useState(c.quiet_hours_start);
  const [qe, setQe] = useState(c.quiet_hours_end);
  const save = useSave<Clinic>("/settings/clinic", "PATCH", "Reminder settings saved", (r) => { setRs(r.reminder_settings); setQs(r.quiet_hours_start); setQe(r.quiet_hours_end); });
  const patch = (k: string, v: object) => setRs({ ...rs, [k]: { ...rs[k], ...v } });
  return (
    <Card title="Reminders and quiet hours" hint="Changes apply to appointments booked or moved from now on. Messages that are due inside quiet hours wait until they end.">
      <form onSubmit={(e) => { e.preventDefault(); save.mutate({ reminder_settings: rs, quiet_hours_start: qs, quiet_hours_end: qe }); }} className="space-y-3">
        {REMINDERS.map((r) => (
          <div key={r.key} className="flex flex-wrap items-center gap-3 rounded-lg border border-[var(--line)] px-3 py-2">
            <label className="flex min-w-[260px] flex-1 items-center gap-2 text-sm"><input type="checkbox" className="accent-teal-600" checked={rs[r.key]?.enabled !== false} onChange={(e) => patch(r.key, { enabled: e.target.checked })} />{r.label}</label>
            {r.offset && <label className="flex items-center gap-2 text-sm"><input type="number" aria-label={`${r.label}: minutes`} className={`${inputCls} !w-24`} value={rs[r.key]?.offset_min ?? ""} onChange={(e) => patch(r.key, { offset_min: Number(e.target.value) })} /><span className="text-xs text-[var(--muted)]">{r.offset}</span></label>}
            {r.key === "daily_summary" && <label className="flex items-center gap-2 text-sm"><input type="time" aria-label="Summary time" className={`${inputCls} !w-28`} value={rs.daily_summary?.at ?? "08:00"} onChange={(e) => patch("daily_summary", { at: e.target.value })} /><span className="text-xs text-[var(--muted)]">each working morning</span></label>}
          </div>))}
        <div className="flex flex-wrap items-center gap-3 text-sm">Quiet hours (no proactive messages)
          <input type="time" aria-label="Quiet hours start" className={`${inputCls} !w-28`} value={qs} onChange={(e) => setQs(e.target.value)} /> to
          <input type="time" aria-label="Quiet hours end" className={`${inputCls} !w-28`} value={qe} onChange={(e) => setQe(e.target.value)} /></div>
        <button className={btnPrimary} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save reminder settings"}</button>
      </form>
    </Card>);
}

// ───────────────────────── doctors ─────────────────────────
function HoursEditor({ value, onChange }: { value: Hours; onChange: (h: Hours) => void }) {
  const upd = (day: string, i: number, j: 0 | 1, v: string) => onChange({ ...value, [day]: value[day].map((r, k) => (k === i ? (j === 0 ? [v, r[1]] : [r[0], v]) : r)) as [string, string][] });
  return (
    <div className="space-y-1.5">
      {DAYS.map(([k, label]) => (
        <div key={k} className="flex flex-wrap items-center gap-2 text-sm">
          <span className="w-24 text-[var(--muted)]">{label}</span>
          {(value[k] ?? []).length === 0 && <span className="text-xs text-[var(--muted)]">Closed</span>}
          {(value[k] ?? []).map((r, i) => (
            <span key={i} className="flex items-center gap-1">
              <input type="time" step={900} aria-label={`${label} start ${i + 1}`} className={`${inputCls} !w-28 !py-1`} value={r[0]} onChange={(e) => upd(k, i, 0, e.target.value)} />–
              <input type="time" step={900} aria-label={`${label} end ${i + 1}`} className={`${inputCls} !w-28 !py-1`} value={r[1]} onChange={(e) => upd(k, i, 1, e.target.value)} />
              <button type="button" aria-label={`Remove ${label} range ${i + 1}`} className="px-1 text-[var(--muted)] hover:text-red-600" onClick={() => onChange({ ...value, [k]: value[k].filter((_, n) => n !== i) })}>×</button>
            </span>))}
          <button type="button" className="text-xs text-teal-700 underline dark:text-teal-300" onClick={() => onChange({ ...value, [k]: [...(value[k] ?? []), ["10:00", "13:00"]] })}>+ add hours</button>
        </div>))}
    </div>);
}

// A private, read-only calendar feed per doctor (subscribe by URL in Google/Apple/Outlook calendar).
function CalendarLink({ d }: { d: Doctor }) {
  const qc = useQueryClient();
  const toast = useToast();
  const reset = useMutation({
    mutationFn: () => api<{ calendar_token: string }>(`/settings/doctors/${d.id}/calendar/reset`, { method: "POST" }),
    onSuccess: () => { toast("New calendar link created. The old one no longer works.", "success"); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e) => toast((e as Error).message, "error"),
  });
  const [confirm, setConfirm] = useState(false);
  if (!d.calendar_token) return null;
  const url = `${typeof window === "undefined" ? "" : window.location.origin}/api/calendar/${d.calendar_token}.ics`;
  const local = typeof window !== "undefined" && /^(localhost|127\.|\[::1\])/.test(window.location.hostname);
  return (
    <div className="rounded-lg bg-black/[0.03] p-3 text-sm dark:bg-white/[0.04]" data-calendar-link>
      <div className="mb-1 font-medium">Calendar link</div>
      <div className="flex flex-wrap gap-2">
        <input readOnly aria-label={`Calendar link for ${d.name}`} className={`${inputCls} min-w-0 flex-1 font-mono text-xs`} value={url} onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className={btnGhost} onClick={() => navigator.clipboard?.writeText(url).then(() => toast("Link copied", "success"), () => toast("Could not copy: select the link and copy it", "error"))}>Copy</button>
        <button type="button" className={btnGhost} onClick={() => setConfirm(true)}>New link…</button>
      </div>
      <p className="mt-2 text-xs text-[var(--muted)]">In Google Calendar: Other calendars → + → From URL. Shows names and times only (no phone numbers or treatments).
        Google refreshes subscribed calendars every few hours, so this is a view, not instant sync. Anyone with this link can see {d.name}&apos;s schedule: keep it private.
        {local && " This address only works on this computer; use the deployed address to subscribe from Google."}</p>
      {confirm && <div role="alertdialog" aria-label="Confirm new calendar link" className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        <span>Calendars already subscribed will stop updating. Continue?</span>
        <button type="button" className={btnPrimary} disabled={reset.isPending} onClick={() => { reset.mutate(); setConfirm(false); }}>Yes, new link</button>
        <button type="button" className={btnGhost} onClick={() => setConfirm(false)}>Keep it</button></div>}
    </div>);
}

function DoctorEditor({ d, onDone }: { d?: Doctor; onDone?: () => void }) {
  const toast = useToast();
  const empty: Hours = Object.fromEntries(DAYS.map(([k]) => [k, []]));
  const [name, setName] = useState(d?.name ?? "");
  const [phone, setPhone] = useState(d?.whatsapp_phone ?? "");
  const [colour, setColour] = useState(d?.colour ?? "#0d9488");
  const [hours, setHours] = useState<Hours>(d?.working_hours ?? empty);
  const [open, setOpen] = useState(!d);
  const save = useSave<Doctor & { appointments_outside_hours?: number }>(d ? `/settings/doctors/${d.id}` : "/settings/doctors", d ? "PATCH" : "POST", d ? `${name} saved` : `${name} added`, (r) => {
    if (r.appointments_outside_hours) toast(`${r.appointments_outside_hours} upcoming appointment(s) now fall outside the new hours. They are unchanged: review them on Today.`, "info");
    if (!d) { setName(""); setPhone(""); setHours(empty); onDone?.(); }
    else { setName(r.name); setPhone(r.whatsapp_phone ?? ""); setColour(r.colour); setHours(r.working_hours); }
  });
  const toggle = useSave<Doctor>(d ? `/settings/doctors/${d.id}` : "/settings/doctors", "PATCH", d?.active ? `${d?.name} deactivated` : `${d?.name} reactivated`);
  return (
    <div className={`rounded-lg border border-[var(--line)] p-3 ${d && !d.active ? "opacity-60" : ""}`} data-doctor-editor={d?.name ?? "new"}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" className="flex items-center gap-2 text-left text-sm font-semibold" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="h-3 w-3 rounded-full" style={{ background: colour }} aria-hidden />{d ? d.name : "Add a doctor"}{d && !d.active && <span className="text-xs font-normal text-[var(--muted)]">(inactive)</span>}
          {d && <span className="text-xs font-normal text-[var(--muted)]">{d.upcoming_appointments} upcoming</span>}</button>
        {d && <button type="button" className={`${btnGhost} !px-3 !py-1 text-xs`} onClick={() => toggle.mutate({ active: !d.active })}>{d.active ? "Deactivate" : "Reactivate"}</button>}
      </div>
      {open && (
        <form className="mt-3 space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate({ name, whatsapp_phone: phone, colour, working_hours: hours }); }}>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Name"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="WhatsApp number (morning summary)"><input className={inputCls} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91…" /></Field>
            <Field label="Colour"><input type="color" className={`${inputCls} !h-10 !p-1`} value={colour} onChange={(e) => setColour(e.target.value)} /></Field>
          </div>
          <div><div className="mb-1 text-sm font-medium">Working hours</div><HoursEditor value={hours} onChange={setHours} /></div>
          {d && <CalendarLink d={d} />}
          {d?.is_demo_seed && <p className="text-xs text-amber-700 dark:text-amber-300">Seeded demo doctor: never messaged on real WhatsApp.</p>}
          <button className={btnPrimary} disabled={save.isPending || !name.trim()}>{save.isPending ? "Saving…" : d ? "Save doctor" : "Add doctor"}</button>
        </form>)}
    </div>);
}

// ───────────────────────── treatments ─────────────────────────
function TreatmentRow({ t }: { t?: Treatment }) {
  const [name, setName] = useState(t?.name ?? "");
  const [dur, setDur] = useState(String(t?.duration_min ?? 30));
  const [recall, setRecall] = useState(t?.recall_after_days ? String(t.recall_after_days) : "");
  const [ticket, setTicket] = useState(String(t?.avg_ticket_value ?? 0));
  const save = useSave<Treatment>(t ? `/settings/treatments/${t.id}` : "/settings/treatments", t ? "PATCH" : "POST", t ? `${name} saved` : `${name} added`, (r: Treatment) => { if (!t) { setName(""); setRecall(""); setTicket("0"); } else { setName(r.name); setDur(String(r.duration_min)); setRecall(r.recall_after_days ? String(r.recall_after_days) : ""); setTicket(String(r.avg_ticket_value)); } });
  return (
    <form className="grid grid-cols-2 items-end gap-2 sm:grid-cols-[1fr_90px_110px_110px_auto]" onSubmit={(e) => { e.preventDefault(); save.mutate({ name, duration_min: Number(dur), recall_after_days: recall ? Number(recall) : null, avg_ticket_value: Number(ticket) || 0 }); }} data-treatment={t?.name ?? "new"}>
      <Field label={t ? "Name" : "New treatment"}><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label="Minutes"><input type="number" step={5} min={5} className={inputCls} value={dur} onChange={(e) => setDur(e.target.value)} /></Field>
      <Field label="Recall (days)"><input type="number" min={1} className={inputCls} value={recall} onChange={(e) => setRecall(e.target.value)} placeholder="none" /></Field>
      <Field label="Ticket (₹)"><input type="number" min={0} className={inputCls} value={ticket} onChange={(e) => setTicket(e.target.value)} /></Field>
      <button className={btnGhost} disabled={save.isPending || !name.trim()}>{t ? "Save" : "Add"}</button>
    </form>);
}

function Templates() {
  const [lang, setLang] = useState("en");
  const q = useQuery({ queryKey: ["settings", "templates", lang], queryFn: () => api<Preview[]>(`/settings/templates?language=${lang}`) });
  return (
    <Card title="Message templates" hint="What patients receive, with sample values. Submitting them to Meta for approval is done under WhatsApp above.">
      <label className="mb-3 flex items-center gap-2 text-sm">Language <select value={lang} onChange={(e) => setLang(e.target.value)} className={`${inputCls} !w-40`}><option value="en">English</option><option value="hi">Hindi</option><option value="mr">Marathi</option></select></label>
      {q.data?.some((t) => t.fallback && t.recipient !== "doctor") && <p className="mb-3 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">Some messages have no {lang === "hi" ? "Hindi" : "Marathi"} text yet: patients who prefer it receive English for those.</p>}
      {lang !== "en" && <p className="mb-3 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200" data-translation-note>These {lang === "hi" ? "Hindi" : "Marathi"} texts are first drafts. Have a native speaker read them (and Meta approve them) before using them with real patients. Dates and times stay in English format.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {q.data?.map((t) => (
          <div key={t.name} className="rounded-lg bg-[#efeae2] p-3 text-gray-900" data-template={t.name}>
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-gray-600"><b>{t.name.replace("showup_", "").replaceAll("_", " ")}</b><span>{t.recipient === "doctor" ? "to the doctor · " : ""}{t.category}</span></div>
            <div className="rounded-lg bg-white px-2.5 py-2 text-[13px] leading-snug shadow-sm">{t.body}{t.footer && <div className="mt-1 text-[11px] text-gray-500">{t.footer}</div>}
              {t.buttons.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{t.buttons.map((b) => <span key={b} className="rounded-full border border-sky-300 px-2 py-0.5 text-[11px] text-sky-700">{b}</span>)}</div>}</div>
            {t.category_note && <p className="mt-1.5 text-[11px] text-amber-800">{t.category_note}</p>}
          </div>))}
      </div>
    </Card>);
}

function Body() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsData>("/settings") });
  const [adding, setAdding] = useState(0);
  useEffect(() => { document.title = "Settings · ShowUp"; }, []);
  if (!q.data) return <div className="mx-auto h-64 max-w-4xl animate-pulse rounded-xl bg-black/5" />;
  const { clinic, doctors, treatment_types } = q.data;
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div><h1 className="text-xl font-semibold">Settings</h1><p className="text-xs text-[var(--muted)]">Owner only. Existing appointments are never changed by anything here.</p></div>
      <WhatsApp />
      <ClinicCard c={clinic} />
      <RemindersCard c={clinic} />
      <Card title="Doctors and working hours" hint="Times are IST on a 15-minute grid. Deactivating a doctor stops new bookings; existing ones stay.">
        <div className="space-y-2">{doctors.map((d) => <DoctorEditor key={d.id} d={d} />)}
          <DoctorEditor key={`new-${adding}`} onDone={() => setAdding(adding + 1)} /></div>
      </Card>
      <Card title="Treatments" hint="Names are for the clinic only and are never sent to patients. The duration is the length of the booking slot; recall is how long after a visit to invite the patient back.">
        <div className="space-y-2">{treatment_types.map((t) => <TreatmentRow key={t.id} t={t} />)}<div className="border-t border-[var(--line)] pt-3"><TreatmentRow key={`n-${treatment_types.length}`} /></div></div>
      </Card>
      <Templates />
    </div>);
}

export default function SettingsPage() {
  return <AuthGate roles={["owner"]}><Body /></AuthGate>;
}
