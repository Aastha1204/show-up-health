"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError, getToken, setToken } from "./api";
import { getSupabase } from "./supabase";

export type Me = { user_id: string; clinic_id: string; role: "owner" | "reception"; email: string | null };
type AuthCtx = { me: Me; token: string; signOut: () => Promise<void> };
const Ctx = createContext<AuthCtx | null>(null);

export const useAuth = () => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthGate");
  return v;
};

/** Renders children only for a signed-in staff member; everyone else goes to /login. The role comes from the
 *  server (/me reads staff_profile), never from the token. Pass `roles` to restrict a page (e.g. Settings, Analytics). */
export function AuthGate({ children, roles }: { children: React.ReactNode; roles?: Me["role"][] }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [token, setTok] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    setTok(getToken());
    const sb = getSupabase();
    if (!sb) return;
    const { data } = sb.auth.onAuthStateChange((_e, session) => {   // keeps the stored token fresh after Supabase refreshes it
      setToken(session?.access_token ?? null);
      setTok(session?.access_token ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const meQ = useQuery({ queryKey: ["me", token], queryFn: () => api<Me>("/me"), enabled: !!token, retry: false, staleTime: 60_000 });

  const signOut = useCallback(async () => {
    await getSupabase()?.auth.signOut();
    setToken(null);
    qc.clear();
    router.replace("/login");
  }, [qc, router]);

  useEffect(() => {
    if (token === null) router.replace("/login");
    if (meQ.error instanceof ApiError && (meQ.error.status === 401 || meQ.error.status === 403)) {
      setToken(null);
      router.replace("/login");
    }
  }, [token, meQ.error, router]);

  if (!token || !meQ.data) {
    return <div className="grid min-h-screen place-items-center text-sm text-[var(--muted)]" role="status">{meQ.isError ? "Cannot reach the server." : "Loading…"}</div>;
  }
  if (roles && !roles.includes(meQ.data.role)) {
    return (
      <div className="mx-auto mt-24 max-w-sm rounded-xl border border-[var(--line)] bg-[var(--surface)] p-6 text-center">
        <h1 className="text-lg font-semibold">Owner access only</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Your role ({meQ.data.role}) cannot open this page.</p>
      </div>
    );
  }
  return <Ctx.Provider value={{ me: meQ.data, token, signOut }}>{children}</Ctx.Provider>;
}
