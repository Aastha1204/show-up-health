"use client";

import { createContext, useContext } from "react";

/** Live updates: every live query polls every 3 s (no push channel is needed for a demo of this size). */
export const POLL_MS = 3000;

type Live = { mode: "realtime" | "polling"; pollMs: number | false };
const Ctx = createContext<Live>({ mode: "polling", pollMs: POLL_MS });
export const useLive = () => useContext(Ctx);

export function LiveProvider({ children }: { children: React.ReactNode }) {
  return <Ctx.Provider value={{ mode: "polling", pollMs: POLL_MS }}>{children}</Ctx.Provider>;
}
