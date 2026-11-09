"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toasts";
import { api } from "./api";
import { useLive } from "./live";
import type { Appt, Doctor, Reference, Status } from "./types";

const VERB: Partial<Record<Status, string>> = { confirmed: "confirmed", cancelled: "cancelled", rescheduled: "rescheduled" };

/** One day's schedule, live. Shared by Today and the /demo presenter view.
 *  Diffs successive polls: a changed card is returned in `highlight` (it pulses), and what the PATIENT just did
 *  raises a toast ("X confirmed via WhatsApp · just now"). The first load of a day announces nothing. */
export function useSchedule(date: string) {
  const live = useLive();
  const toast = useToast();
  const [highlight, setHighlight] = useState<Set<string>>(new Set());

  const ref = useQuery({ queryKey: ["reference"], queryFn: () => api<Reference>("/reference"), staleTime: 60_000 });
  const apptsQ = useQuery({ queryKey: ["appts", date], queryFn: () => api<Appt[]>(`/appointments?date=${date}`), refetchInterval: live.pollMs });
  const callQ = useQuery({ queryKey: ["call-list"], queryFn: () => api<Appt[]>("/appointments/call-list"), refetchInterval: live.pollMs });
  const appts = apptsQ.data;

  const seen = useRef<{ date: string; map: Map<string, { status: Status; at: string | null }> } | null>(null);
  useEffect(() => {
    if (!appts) return;
    const next = new Map(appts.map((a) => [a.id, { status: a.status, at: a.last_event_at }]));
    const prev = seen.current;
    seen.current = { date, map: next };
    if (!prev || prev.date !== date) return;
    const changed: string[] = [];
    for (const a of appts) {
      const before = prev.map.get(a.id);
      if (!before) {
        changed.push(a.id);
        if (a.source === "waitlist" && a.last_event_actor === "patient") toast(`${a.patient_name} booked a freed slot via WhatsApp · just now`, "success");
        continue;
      }
      if (before.at !== a.last_event_at || before.status !== a.status) {
        changed.push(a.id);
        const verb = VERB[a.status];
        if (verb && a.status !== before.status && a.last_event_actor !== "reception") toast(`${a.patient_name} ${verb} via WhatsApp · just now`, "success");
      }
    }
    if (changed.length) {
      setHighlight((h) => new Set([...h, ...changed]));
      setTimeout(() => setHighlight((h) => { const n = new Set(h); changed.forEach((id) => n.delete(id)); return n; }), 2800);
    }
  }, [appts, date, toast]);

  const counts = useMemo(() => {
    const c = { total: 0, confirmed: 0, pending: 0, cancelled: 0, arrived: 0, rescheduled: 0, no_show: 0 };
    for (const a of appts ?? []) { c[a.status]++; if (a.status !== "cancelled") c.total++; }
    return c;
  }, [appts]);

  const doctors: Doctor[] = ref.data?.doctors ?? [];
  return { ref, apptsQ, callQ, appts, doctors, highlight, counts };
}
