"use client";

import { fmtTime, hhmmToMin, isoFromIst, istDate, istMinutes, minLabel, weekdayKey } from "@/lib/time";
import { SOURCE_ICON, STATUS_LABEL, type Appt, type Doctor, type Status } from "@/lib/types";
import { StatusChip } from "./ui";

const PX = 2;            // pixels per minute: a 15-minute row is 30px
const BAND = 28;         // height of a "Closed" band between working blocks
const HEAD = 44;
const ACTIVE = new Set<Status>(["pending", "confirmed", "rescheduled", "arrived"]);
type Range = [number, number];
type Block = { start: number; end: number; top: number };

function merge(ranges: Range[]): Range[] {
  const out: Range[] = [];
  for (const r of [...ranges].sort((a, b) => a[0] - b[0])) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

type Placed = { a: Appt; s: number; e: number; lane: number; lanes: number };
/** Standard calendar lanes: overlapping cards sit side by side instead of on top of each other. */
function lanes(list: Appt[]): Placed[] {
  const items: Placed[] = list.map((a) => ({ a, s: istMinutes(a.start_at), e: istMinutes(a.end_at), lane: 0, lanes: 1 }))
    .sort((x, y) => x.s - y.s || x.e - y.e);
  let cluster: Placed[] = [], laneEnds: number[] = [], clusterEnd = -1;
  const flush = () => { cluster.forEach((c) => (c.lanes = Math.max(1, laneEnds.length))); cluster = []; laneEnds = []; clusterEnd = -1; };
  for (const it of items) {
    if (cluster.length && it.s >= clusterEnd) flush();
    let lane = laneEnds.findIndex((end) => end <= it.s);
    if (lane === -1) { lane = laneEnds.length; laneEnds.push(it.e); } else laneEnds[lane] = it.e;
    it.lane = lane;
    cluster.push(it);
    clusterEnd = Math.max(clusterEnd, it.e);
  }
  flush();
  return items;
}

type Props = {
  date: string; doctors: Doctor[]; appts: Appt[]; nowMs: number; highlight: Set<string>; minGap?: number;
  onOpen: (a: Appt) => void; onGap?: (doctor: Doctor, startIso: string, minutes: number) => void;   // no onGap: gaps are display-only
  selectedId?: string | null;
};

export default function DayGrid({ date, doctors, appts, nowMs, highlight, minGap = 20, onOpen, onGap, selectedId }: Props) {
  const wk = weekdayKey(date);
  const own = (d: Doctor): Range[] => (d.working_hours?.[wk] ?? []).map(([a, b]) => [hhmmToMin(a), hhmmToMin(b)]);
  const today = istDate(new Date(nowMs)), nowMin = istMinutes(new Date(nowMs));

  // visible time = every doctor's working hours plus any appointment that falls outside them
  const merged = merge([...doctors.flatMap(own), ...appts.map((a): Range => [istMinutes(a.start_at), istMinutes(a.end_at)])]);
  const blocks: Block[] = [];
  merged.forEach(([start, end], i) => blocks.push({ start, end, top: i === 0 ? 0 : blocks[i - 1].top + (blocks[i - 1].end - blocks[i - 1].start) * PX + BAND }));
  const H = blocks.length ? blocks[blocks.length - 1].top + (blocks[blocks.length - 1].end - blocks[blocks.length - 1].start) * PX : 0;
  const yOf = (min: number) => {
    const b = blocks.find((x) => min >= x.start && min <= x.end) ?? blocks.filter((x) => x.end <= min).pop() ?? blocks[0];
    return b.top + (Math.min(Math.max(min, b.start), b.end) - b.start) * PX;
  };

  const gapsFor = (d: Doctor): Range[] => {
    if (date < today) return [];
    const busy = appts.filter((a) => a.doctor_id === d.id && ACTIVE.has(a.status))
      .map((a): Range => [istMinutes(a.start_at), istMinutes(a.end_at)]).sort((x, y) => x[0] - y[0]);
    const out: Range[] = [];
    for (const [rs, re] of own(d)) {
      let cur = date === today ? Math.max(rs, Math.ceil(nowMin / 15) * 15) : rs;
      for (const [bs, be] of busy) {
        if (be <= cur || bs >= re) continue;
        if (bs - cur >= minGap) out.push([cur, bs]);
        cur = Math.max(cur, be);
      }
      if (re - cur >= minGap) out.push([cur, re]);
    }
    return out;
  };
  const hatchFor = (d: Doctor): Range[] => {         // parts of each visible block this doctor does not work
    const mine = merge(own(d)), out: Range[] = [];
    for (const b of blocks) {
      let cur = b.start;
      for (const [s, e] of mine) { if (e <= cur || s >= b.end) continue; if (s > cur) out.push([cur, Math.min(s, b.end)]); cur = Math.max(cur, e); }
      if (cur < b.end) out.push([cur, b.end]);
    }
    return out;
  };

  if (!blocks.length) return null;
  const cols = `56px repeat(${doctors.length}, minmax(230px, 1fr))`;
  const nowInside = date === today && blocks.some((b) => nowMin >= b.start && nowMin <= b.end);

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--line)] bg-[var(--surface)]">
      <div style={{ minWidth: 56 + doctors.length * 230 }}>
        <div className="sticky top-0 z-20 grid border-b border-[var(--line)] bg-[var(--surface)]" style={{ gridTemplateColumns: cols, height: HEAD }}>
          <div />
          {doctors.map((d) => {
            const n = appts.filter((a) => a.doctor_id === d.id && a.status !== "cancelled").length;
            return (
              <div key={d.id} className="flex items-center gap-2 border-l border-[var(--line)] px-3">
                <span className="h-3 w-3 rounded-full" style={{ background: d.colour }} aria-hidden />
                <span className="truncate text-sm font-semibold">{d.name}</span>
                <span className="ml-auto text-xs text-[var(--muted)]">{n} booked</span>
              </div>
            );
          })}
        </div>

        <div className="relative grid" style={{ gridTemplateColumns: cols, height: H }}>
          {/* time axis */}
          <div className="relative">
            {blocks.flatMap((b) => {
              const ticks = [];
              for (let m = Math.ceil(b.start / 30) * 30; m < b.end; m += 30)
                ticks.push(<div key={m} className="absolute right-2 -translate-y-1/2 text-[10px] text-[var(--muted)]" style={{ top: yOf(m) }}>{m % 60 === 0 ? minLabel(m) : ""}</div>);
              return ticks;
            })}
          </div>

          {doctors.map((d) => {
            const mine = lanes(appts.filter((a) => a.doctor_id === d.id));
            return (
              <div key={d.id} className="relative border-l border-[var(--line)]" data-doctor={d.name}>
                {/* hour lines */}
                {blocks.flatMap((b) => {
                  const lines = [];
                  for (let m = Math.ceil(b.start / 60) * 60; m < b.end; m += 60)
                    lines.push(<div key={m} className="absolute inset-x-0 border-t border-[var(--line)]/70" style={{ top: yOf(m) }} />);
                  return lines;
                })}
                {hatchFor(d).map(([s, e], i) => (
                  <div key={i} className="closed-hatch absolute inset-x-0" style={{ top: yOf(s), height: (e - s) * PX }} aria-hidden />
                ))}
                {gapsFor(d).map(([s, e]) => (
                  <button key={`${s}-${e}`} onClick={() => onGap?.(d, isoFromIst(date, s), e - s)} disabled={!onGap} aria-label={`Open slot ${minLabel(s)} to ${minLabel(e)} with ${d.name}`}
                    className="gap-block absolute inset-x-1 z-0 rounded-md px-2 py-1 text-left text-xs transition-colors disabled:cursor-default" style={{ top: yOf(s) + 1, height: (e - s) * PX - 2 }}>
                    {(e - s) * PX >= 44 ? <><span className="font-medium">Open slot</span><br /><span className="opacity-80">{minLabel(s)} – {minLabel(e)}</span></> : <span className="font-medium">Open · {minLabel(s)}</span>}
                  </button>
                ))}
                {mine.map(({ a, s, e, lane, lanes: n }) => {
                  const w = 100 / n;
                  return (
                    <button key={a.id} onClick={() => onOpen(a)} data-appt={a.id} data-status={a.status}
                      aria-label={`${a.patient_name}, ${fmtTime(a.start_at)}, ${STATUS_LABEL[a.status]}`}
                      className={`st st-${a.status} absolute z-10 overflow-hidden rounded-md px-2 py-1 text-left text-xs shadow-sm transition-[background,box-shadow] hover:shadow-md ${highlight.has(a.id) ? "card-pulse" : ""} ${selectedId === a.id ? "ring-2 ring-teal-500 ring-offset-1 ring-offset-[var(--surface)]" : ""}`}
                      style={{ top: yOf(s) + 1, height: Math.max((e - s) * PX - 2, 36), left: `calc(${lane * w}% + 2px)`, width: `calc(${w}% - 4px)` }}>
                      <div className="flex items-center justify-between gap-1">
                        <span className="name truncate font-semibold">{a.patient_name}</span>
                        <span title={SOURCE_ICON[a.source].label} aria-label={SOURCE_ICON[a.source].label} className="shrink-0 text-[11px]">{SOURCE_ICON[a.source].icon}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 whitespace-nowrap opacity-95">
                        <span>{fmtTime(a.start_at)}</span>
                        <span className="opacity-80">•••• {a.patient_phone.slice(-4)}</span>
                        {a.patient_no_show_count >= 2 && <span title={`${a.patient_no_show_count} previous no-shows`} className="rounded bg-black/20 px-1 font-bold">⚠{a.patient_no_show_count}</span>}
                        {a.patient_opted_out && <span title="Opted out of WhatsApp">🔕</span>}
                        {n === 1 && (e - s) * PX >= 60 && <StatusChip status={a.status} className="ml-auto" />}
                      </div>
                    </button>
                  );
                })}
              </div>
            );
          })}

          {/* closed bands between working blocks */}
          {blocks.slice(0, -1).map((b, i) => (
            <div key={i} className="pointer-events-none absolute inset-x-0 z-[5] flex items-center justify-center border-y border-[var(--line)] bg-[var(--bg)] text-[11px] font-medium text-[var(--muted)]"
              style={{ top: b.top + (b.end - b.start) * PX, height: BAND }}>
              Closed · {minLabel(b.end)} – {minLabel(blocks[i + 1].start)}
            </div>
          ))}

          {nowInside && (
            <div className="pointer-events-none absolute inset-x-0 z-30 border-t-2 border-red-500" style={{ top: yOf(nowMin) }} aria-label="Current time">
              <span className="absolute -left-0 -top-2 rounded bg-red-500 px-1 text-[10px] font-bold text-white">{minLabel(nowMin)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
