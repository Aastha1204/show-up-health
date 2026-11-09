"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { addDays, fmtDay } from "@/lib/time";

export type Week = { week_start: string; completed: number; no_shows: number; no_show_rate: number | null; recovered: number };

const tick = { fill: "var(--viz-muted)", fontSize: 11 };
const lbl = (w: Week) => fmtDay(w.week_start).replace(/^\w+ /, "");        // "9 Nov"
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

function Card({ title, subtitle, table, children, summary }: { title: string; subtitle: string; table: React.ReactNode; children: React.ReactNode; summary: string }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="viz-root viz-card p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs" style={{ color: "var(--viz-ink-2)" }}>{subtitle}</p>
        </div>
        <button onClick={() => setAsTable(!asTable)} className="rounded-md border border-[var(--line)] px-2.5 py-1 text-xs font-medium hover:bg-black/5" aria-pressed={asTable}>
          {asTable ? "Show chart" : "View as table"}
        </button>
      </div>
      {asTable ? <div className="max-h-[260px] overflow-y-auto">{table}</div> : <div role="img" aria-label={summary}>{children}</div>}
    </section>
  );
}

function Tip({ active, payload, render }: { active?: boolean; payload?: { payload: Week }[]; render: (w: Week) => React.ReactNode }) {
  if (!active || !payload?.length) return null;
  const w = payload[0].payload;
  return (
    <div className="rounded-lg border px-3 py-2 text-xs shadow-lg" style={{ background: "var(--viz-surface)", borderColor: "var(--viz-axis)", color: "var(--viz-ink)" }}>
      <div className="mb-1 font-semibold">Week of {fmtDay(w.week_start)}</div>
      {render(w)}
    </div>
  );
}

function Table({ weekly, cols }: { weekly: Week[]; cols: { head: string; cell: (w: Week) => string }[] }) {
  return (
    <table className="w-full text-xs tabular-nums">
      <thead><tr className="text-left" style={{ color: "var(--viz-ink-2)" }}><th className="py-1 pr-3 font-medium">Week of</th>{cols.map((c) => <th key={c.head} className="py-1 pr-3 font-medium">{c.head}</th>)}</tr></thead>
      <tbody>{weekly.map((w) => <tr key={w.week_start} className="border-t" style={{ borderColor: "var(--viz-grid)" }}><td className="py-1 pr-3">{fmtDay(w.week_start)}</td>{cols.map((c) => <td key={c.head} className="py-1 pr-3">{c.cell(w)}</td>)}</tr>)}</tbody>
    </table>
  );
}

export function NoShowChart({ weekly, baseline, goLive }: { weekly: Week[]; baseline: number; goLive: string | null }) {
  const data = weekly.map((w) => ({ ...w, label: lbl(w), pct: w.no_show_rate === null ? null : +(w.no_show_rate * 100).toFixed(2) }));
  const max = Math.max(baseline * 100 * 1.4, ...data.map((d) => d.pct ?? 0));
  const goLiveWeek = goLive ? weekly.find((w) => goLive >= w.week_start && goLive < addDays(w.week_start, 7)) : undefined;
  const measured = data.filter((d) => d.pct !== null);
  const summary = measured.length
    ? `Weekly no-show rate, from ${measured[0].pct}% in the week of ${fmtDay(measured[0].week_start)} to ${measured[measured.length - 1].pct}% in the week of ${fmtDay(measured[measured.length - 1].week_start)}; baseline ${(baseline * 100).toFixed(0)}%.`
    : "Weekly no-show rate: no completed visits yet.";
  return (
    <Card title="No-show rate, by week" subtitle="Share of completed visits that were no-shows. Weeks with no completed visits are left blank." summary={summary}
      table={<Table weekly={weekly} cols={[{ head: "No-show rate", cell: (w) => (w.no_show_rate === null ? "n/a" : pct(w.no_show_rate)) }, { head: "No-shows", cell: (w) => String(w.no_shows) }, { head: "Completed", cell: (w) => String(w.completed) }]} />}>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 22, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={{ stroke: "var(--viz-axis)" }} interval="preserveStartEnd" minTickGap={18} />
          <YAxis tick={tick} tickLine={false} axisLine={false} width={40} domain={[0, Math.ceil(max / 5) * 5]} tickFormatter={(v) => `${v}%`} />
          <Tooltip cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }}
            content={<Tip render={(w) => w.no_show_rate === null ? <div>No completed visits</div> : <><div><b>{pct(w.no_show_rate)}</b> no-show rate</div><div style={{ color: "var(--viz-ink-2)" }}>{w.no_shows} no-shows of {w.completed} completed visits</div></>} />} />
          <ReferenceLine y={baseline * 100} stroke="var(--viz-muted)" strokeDasharray="4 4" label={{ value: `Baseline ${(baseline * 100).toFixed(0)}%`, position: "insideTopRight", fill: "var(--viz-ink-2)", fontSize: 11 }} />
          {goLiveWeek && <ReferenceLine x={lbl(goLiveWeek)} stroke="var(--viz-ink-2)" strokeWidth={1} label={{ value: "Reminders went live", position: "top", fill: "var(--viz-ink-2)", fontSize: 11 }} />}
          <Line type="linear" dataKey="pct" stroke="var(--viz-series)" strokeWidth={2} connectNulls={false} isAnimationActive={false}
            dot={{ r: 4, fill: "var(--viz-series)", stroke: "var(--viz-surface)", strokeWidth: 2 }} activeDot={{ r: 5, fill: "var(--viz-series)", stroke: "var(--viz-surface)", strokeWidth: 2 }} />
        </LineChart>
      </ResponsiveContainer>
    </Card>
  );
}

export function RecoveredChart({ weekly }: { weekly: Week[] }) {
  const data = weekly.map((w) => ({ ...w, label: lbl(w) }));
  const total = weekly.reduce((n, w) => n + w.recovered, 0);
  return (
    <Card title="Slots recovered from the waitlist, by week" subtitle="Cancelled or freed slots that a waitlisted patient then booked."
      summary={`Slots recovered per week; ${total} in the last 13 weeks.`}
      table={<Table weekly={weekly} cols={[{ head: "Slots recovered", cell: (w) => String(w.recovered) }]} />}>
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={data} margin={{ top: 20, right: 16, bottom: 4, left: 0 }} barCategoryGap="30%">
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={{ stroke: "var(--viz-axis)" }} interval="preserveStartEnd" minTickGap={18} />
          <YAxis tick={tick} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
          <Tooltip cursor={{ fill: "var(--viz-grid)", opacity: 0.5 }} content={<Tip render={(w) => <div><b>{w.recovered}</b> slot{w.recovered === 1 ? "" : "s"} recovered</div>} />} />
          <Bar dataKey="recovered" fill="var(--viz-series)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false}>
            <LabelList dataKey="recovered" position="top" fill="var(--viz-ink-2)" fontSize={11} formatter={(v: number) => (v > 0 ? v : "")} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}
