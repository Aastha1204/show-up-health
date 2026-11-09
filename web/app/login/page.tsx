"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { post, setToken } from "@/lib/api";
import { getSupabase, supabaseConfigured } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function supabaseSignIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const { data, error } = await getSupabase()!.auth.signInWithPassword({ email: email.trim(), password });
    setBusy(false);
    if (error || !data.session) return setErr("That email and password do not match.");
    setToken(data.session.access_token);
    router.replace("/today");
  }

  async function devSignIn(role: "reception" | "owner") {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ access_token: string }>("/auth/dev-login", { role });
      setToken(r.access_token);
      router.replace("/today");
    } catch (e) {
      setErr((e as Error).message === "Not Found" ? "Local sign-in is switched off on this server." : (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-6 shadow-sm">
        <div className="mb-5 flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-teal-600 text-lg font-bold text-white" aria-hidden>S</span>
          <div>
            <h1 className="text-lg font-semibold leading-tight">ShowUp</h1>
            <p className="text-xs text-[var(--muted)]">by Neovia AI · SmileCare Dental Clinic</p>
          </div>
        </div>

        {supabaseConfigured ? (
          <form onSubmit={supabaseSignIn} className="space-y-3">
            <label className="block text-sm">Email
              <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-transparent px-3 py-2" />
            </label>
            <label className="block text-sm">Password
              <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded-lg border border-[var(--line)] bg-transparent px-3 py-2" />
            </label>
            <button disabled={busy} className="w-full rounded-lg bg-teal-600 py-2.5 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-60">
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-xs text-[var(--muted)]">Demo users: owner@smilecare.demo and reception@smilecare.demo (passwords are in the README).</p>
          </form>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-[var(--muted)]">Local demo mode (no Supabase project configured).</p>
            <button disabled={busy} onClick={() => devSignIn("reception")} className="w-full rounded-lg bg-teal-600 py-2.5 text-sm font-semibold text-white hover:bg-teal-700 disabled:opacity-60">Sign in as reception</button>
            <button disabled={busy} onClick={() => devSignIn("owner")} className="w-full rounded-lg border border-[var(--line)] py-2.5 text-sm font-semibold hover:bg-black/5 disabled:opacity-60">Sign in as owner</button>
          </div>
        )}
        {err && <p role="alert" className="mt-3 text-sm text-red-600">{err}</p>}
      </div>
    </main>
  );
}
