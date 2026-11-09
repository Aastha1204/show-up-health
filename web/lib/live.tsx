"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { getToken } from "./api";
import { getSupabase } from "./supabase";

/** Live updates. With Supabase configured we subscribe to Realtime postgres_changes and invalidate queries on any
 *  change; if the channel is not SUBSCRIBED (or Supabase is not configured) every live query polls every 3 s. */
const TABLES = ["appointment", "outbound_message", "message_event", "waitlist_offer", "inbox_item"];
export const POLL_MS = 3000;

type Live = { mode: "realtime" | "polling"; pollMs: number | false };
const Ctx = createContext<Live>({ mode: "polling", pollMs: POLL_MS });
export const useLive = () => useContext(Ctx);

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Live["mode"]>("polling");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const sb = getSupabase();
    const token = getToken();
    if (!sb || !token) return;
    const bump = () => {                                   // many row changes arrive together: refresh once
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => qc.invalidateQueries(), 250);
    };
    sb.realtime.setAuth(token);
    const ch = sb.channel("showup-live");
    TABLES.forEach((table) => ch.on("postgres_changes", { event: "*", schema: "public", table }, bump));
    ch.subscribe((status) => setMode(status === "SUBSCRIBED" ? "realtime" : "polling"));   // channel drops -> poll again
    return () => { if (timer.current) clearTimeout(timer.current); sb.removeChannel(ch); };
  }, [qc]);

  return <Ctx.Provider value={{ mode, pollMs: mode === "realtime" ? false : POLL_MS }}>{children}</Ctx.Provider>;
}
