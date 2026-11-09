"use client";

import { useMemo, useState } from "react";
import AppointmentDrawer from "@/components/AppointmentDrawer";
import AppointmentForm, { type FormMode } from "@/components/AppointmentForm";
import CallList from "@/components/CallList";
import DayGrid from "@/components/DayGrid";
import RunningLateModal from "@/components/RunningLateModal";
import { btnGhost, btnPrimary, Empty } from "@/components/ui";
import { useNow } from "@/lib/hooks";
import { useSchedule } from "@/lib/useSchedule";
import { addDays, fmtDay, fmtDayLong, istDate, weekdayKey } from "@/lib/time";

function Count({ label, n, tone }: { label: string; n: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-center" data-count={label.toLowerCase()}>
      <div className={`text-lg font-bold leading-tight tabular-nums ${tone ?? ""}`}>{n}</div>
      <div className="text-[11px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
    </div>
  );
}

export default function TodayPage() {
  const nowMs = useNow(15_000);
  const [date, setDate] = useState(() => istDate());
  const [openId, setOpenId] = useState<string | null>(null);
  const [form, setForm] = useState<FormMode | null>(null);
  const [late, setLate] = useState(false);
  const { ref, apptsQ, callQ, appts, doctors, highlight, counts } = useSchedule(date);

  const opened = useMemo(() => appts?.find((a) => a.id === openId) ?? callQ.data?.find((a) => a.id === openId) ?? null, [appts, callQ.data, openId]);
  const works = (d: string) => doctors.some((doc) => (doc.working_hours?.[weekdayKey(d)] ?? []).length > 0);
  const closed = !!ref.data && !!appts && !works(date) && appts.length === 0;
  const nextWorking = () => { for (let i = 1; i <= 7; i++) if (works(addDays(date, i))) return addDays(date, i); return null; };
  const today = istDate(new Date(nowMs));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-3">
        <div>
          <h1 className="text-xl font-semibold">{date === today ? "Today" : fmtDay(date)}</h1>
          <p className="text-xs text-[var(--muted)]">{fmtDayLong(date)} · times in IST</p>
        </div>
        <div className="flex items-center gap-1.5">
          <button className={btnGhost} onClick={() => setDate(addDays(date, -1))} aria-label="Previous day">‹</button>
          <input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} aria-label="Date"
            className="rounded-lg border border-[var(--line)] bg-transparent px-3 py-2 text-sm" />
          <button className={btnGhost} onClick={() => setDate(addDays(date, 1))} aria-label="Next day">›</button>
          <button className={btnGhost} onClick={() => setDate(today)} disabled={date === today}>Today</button>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="Counts for the day">
          <Count label="Total" n={counts.total} />
          <Count label="Confirmed" n={counts.confirmed} tone="text-emerald-600 dark:text-emerald-400" />
          <Count label="Pending" n={counts.pending} tone="text-amber-600 dark:text-amber-400" />
          <Count label="Cancelled" n={counts.cancelled} tone="text-red-600 dark:text-red-400" />
          <Count label="Arrived" n={counts.arrived} tone="text-teal-600 dark:text-teal-400" />
          {counts.rescheduled > 0 && <Count label="Rescheduled" n={counts.rescheduled} tone="text-blue-600 dark:text-blue-400" />}
          {counts.no_show > 0 && <Count label="No-show" n={counts.no_show} tone="text-red-800 dark:text-red-300" />}
        </div>
        <div className="ml-auto flex gap-2">
          <button className={btnGhost} onClick={() => setLate(true)} disabled={!doctors.length}>Running late</button>
          <button className={btnPrimary} onClick={() => setForm({ mode: "new" })}>+ Add appointment</button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div>
          {apptsQ.isError ? <p role="alert" className="rounded-lg bg-red-500/10 p-4 text-sm text-red-700 dark:text-red-300">Could not load the schedule. Retrying…</p>
            : !appts || !ref.data ? <div className="h-72 animate-pulse rounded-xl bg-black/5" aria-label="Loading schedule" />
            : closed ? (
              <Empty>
                The clinic is closed on {fmtDayLong(date)}.
                {nextWorking() && <div className="mt-3"><button className={btnPrimary} onClick={() => setDate(nextWorking()!)}>Go to {fmtDay(nextWorking()!)}</button></div>}
              </Empty>
            ) : (
              <DayGrid date={date} doctors={doctors} appts={appts} nowMs={nowMs} highlight={highlight}
                onOpen={(a) => setOpenId(a.id)}
                onGap={(d, startIso) => setForm({ mode: "new", prefill: { doctorId: d.id, startIso } })} />
            )}
          <p className="mt-2 text-xs text-[var(--muted)]">Click a card for details and actions, or a dashed “Open slot” to book it.</p>
        </div>
        <CallList items={callQ.data ?? []} nowMs={nowMs} onOpen={(a) => setOpenId(a.id)} />
      </div>

      {opened && <AppointmentDrawer appt={opened} nowMs={nowMs} onClose={() => setOpenId(null)} onReschedule={(a) => setForm({ mode: "edit", appt: a })} />}
      {form && <AppointmentForm key={form.mode === "edit" ? form.appt.id : "new"} state={form} onClose={() => setForm(null)} />}
      {late && doctors.length > 0 && <RunningLateModal doctors={doctors} onClose={() => setLate(false)} />}
    </div>
  );
}
