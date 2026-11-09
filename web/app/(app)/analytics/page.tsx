"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NoShowChart, RecoveredChart, type Week } from "@/components/Charts";
import { api } from "@/lib/api";
import { AuthGate } from "@/lib/auth";
import { useLive } from "@/lib/live";
import { istDate } from "@/lib/time";

type Analytics = {
  month: string; simulated_share: number;
  clinic: { baseline_no_show_rate: number; avg_ticket_value: number; go_live_date: string | null; plan_price: number };
  tiles: { no_show_rate: number | null; completed: number; no_shows: number; recovered_slots: number; recovered_since_go_live: number;
           revenue_saved: number; confirmation_rate: number | null; confirmed_via_whatsapp: number; settled: number };
  weekly: Week[];
  roi: { recovered: number; avoided_no_shows: number; saved_appointments: number; value: number; plan_price: number; no_show_rate: number | null; window_from: string; window_to: string };
};

const inr = (n: number) => `₹${new Intl.NumberFormat("en-IN").format(n)}`;
const pct = (x: number, d = 1) => `${(x * 100).toFixed(d)}%`;
const monthLabel = (m: string) => new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "long", year: "numeric" });

function Tile({ label, value, sub, foot }: { label: string; value: string; sub?: string; foot?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4" data-tile={label}>
      <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <div className="mt-1 text-3xl font-bold leading-tight">{value}</div>
      {sub && <div className="mt-1 text-xs text-[var(--muted)]">{sub}</div>}
      {foot && <div className="mt-2 text-xs">{foot}</div>}
    </div>
  );
}

function Body() {
  const live = useLive();
  const [month, setMonth] = useState(() => istDate().slice(0, 7));
  const q = useQuery({ queryKey: ["analytics", month], queryFn: () => api<Analytics>(`/analytics?month=${month}`), refetchInterval: live.pollMs === false ? 30_000 : 10_000 });
  const a = q.data;

  const simulated = a && a.simulated_share > 0
    ? (a.simulated_share >= 0.999 ? "Simulated data" : `Includes simulated data (${Math.round(a.simulated_share * 100)}%)`) : null;

  return (
    <div className="viz-root mx-auto max-w-6xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Analytics</h1>
          {simulated && <span data-testid="simulated-badge" title="These numbers come from the seeded demo patients, not real visits." className="rounded-full border border-amber-500/50 bg-amber-500/15 px-2.5 py-0.5 text-xs font-semibold text-amber-800 dark:text-amber-200">{simulated}</span>}
        </div>
        <label className="text-sm">Month <input type="month" value={month} max={istDate().slice(0, 7)} onChange={(e) => e.target.value && setMonth(e.target.value)} className="ml-1 rounded-lg border border-[var(--line)] bg-transparent px-3 py-1.5 text-sm" /></label>
      </div>

      {!a ? <div className="h-64 animate-pulse rounded-xl bg-black/5" /> : (() => {
        const t = a.tiles, base = a.clinic.baseline_no_show_rate, r = a.roi;
        const delta = t.no_show_rate === null ? null : (t.no_show_rate - base) * 100;
        const ratio = r.value / r.plan_price;
        return (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Tile label="No-show rate" value={t.no_show_rate === null ? "—" : pct(t.no_show_rate)}
                sub={t.no_show_rate === null ? `No completed visits in ${monthLabel(a.month)} yet` : `${t.no_shows} no-shows of ${t.completed} completed visits · baseline ${pct(base, 0)}`}
                foot={delta !== null && Math.abs(delta) >= 0.05 && (
                  <span className="font-semibold" style={{ color: delta < 0 ? "var(--viz-good)" : "var(--viz-critical)" }}>
                    {delta < 0 ? "▼" : "▲"} {Math.abs(delta).toFixed(1)} pts {delta < 0 ? "below" : "above"} baseline
                  </span>)} />
              <Tile label="Slots recovered" value={String(t.recovered_slots)} sub={`in ${monthLabel(a.month)} · ${t.recovered_since_go_live} since reminders went live`}
                foot={<span className="text-[var(--muted)]">Freed slots a waitlisted patient booked</span>} />
              <Tile label="Revenue saved" value={inr(t.revenue_saved)} sub={`${t.recovered_slots} recovered slots × ${inr(a.clinic.avg_ticket_value)} average ticket`}
                foot={<span className="text-[var(--muted)]">Formula: recovered slots × average ticket</span>} />
              <Tile label="WhatsApp confirmation rate" value={t.confirmation_rate === null ? "—" : pct(t.confirmation_rate, 0)}
                sub={t.confirmation_rate === null ? "No settled appointments yet" : `${t.confirmed_via_whatsapp} of ${t.settled} appointments confirmed by the patient on WhatsApp`} />
            </div>

            <section className="mt-4 rounded-xl border border-teal-600/40 bg-teal-600/10 p-5" aria-label="Return on investment" data-testid="roi">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-teal-800 dark:text-teal-200">What ShowUp is worth to this clinic</h2>
              {/* Lead with what was MEASURED (slots recovered from the waitlist). The no-shows avoided are an estimate, shown as upside. */}
              {(() => {
                const measured = r.recovered * a.clinic.avg_ticket_value;
                const covers = measured >= r.plan_price;
                const headCount = covers ? r.recovered : r.saved_appointments;
                const headValue = covers ? measured : r.value;
                return (
                  <>
                    <p className="mt-2 text-xl font-semibold" data-testid="roi-formula">
                      {headCount} saved appointment{headCount === 1 ? "" : "s"} × {inr(a.clinic.avg_ticket_value)} = {inr(headValue)}
                      <span className="font-normal text-[var(--muted)]"> vs a {inr(r.plan_price)} plan</span>
                    </p>
                    <p className="mt-1 text-sm">
                      {headValue >= r.plan_price ? `That is ${(headValue / r.plan_price).toFixed(1)}× the monthly price: the system pays for itself.` : "Not yet covering the plan price over this period."}
                      {covers ? " Counting only slots recovered from the waitlist (measured)." : " Includes an estimate (below)."}
                    </p>
                    {r.avoided_no_shows > 0 && (
                      <p className="mt-2 text-sm" data-testid="roi-estimate">
                        <b>Plus an estimated {r.avoided_no_shows} no-show{r.avoided_no_shows === 1 ? "" : "s"} avoided</b> ({inr(r.avoided_no_shows * a.clinic.avg_ticket_value)}):
                        your no-show rate is {r.no_show_rate === null ? "n/a" : pct(r.no_show_rate)} against a {pct(base, 0)} baseline.
                      </p>
                    )}
                    <p className="mt-2 text-xs text-[var(--muted)]">
                      Window: {r.window_from} to {r.window_to} (the last 30 days plus bookings already made for the next 7).
                      Slots recovered are counted; no-shows avoided are an estimate against your own baseline, not a measured count.
                    </p>
                  </>
                );
              })()}
            </section>

            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <NoShowChart weekly={a.weekly} baseline={base} goLive={a.clinic.go_live_date} />
              <RecoveredChart weekly={a.weekly} />
            </div>
          </>
        );
      })()}
    </div>
  );
}

export default function AnalyticsPage() {
  return <AuthGate roles={["owner"]}><Body /></AuthGate>;
}
