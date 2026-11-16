"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth, type Me } from "@/lib/auth";
import { useLive } from "@/lib/live";

type NavItem = { href: string; label: string; roles?: Me["role"][] };
// `roles` hides a link (and the page itself is also gated with AuthGate roles). Analytics and Settings are owner-only.
const NAV: NavItem[] = [
  { href: "/today", label: "Today" },
  { href: "/waitlist", label: "Waitlist" },
  { href: "/patients", label: "Patients" },
  { href: "/inbox", label: "Inbox" },
  { href: "/import", label: "Import" },
  { href: "/analytics", label: "Analytics", roles: ["owner"] },
  { href: "/settings", label: "Settings", roles: ["owner"] },
  { href: "/simulator", label: "Simulator" },
  { href: "/demo", label: "Demo" },
];
/** On a phone these four live in a bottom tab bar; everything else is under "More". */
const PRIMARY = ["/today", "/waitlist", "/patients", "/inbox"];

function Icon({ name }: { name: string }) {
  const p = { width: 22, height: 22, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.9, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "/today": return <svg {...p}><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></svg>;
    case "/waitlist": return <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
    case "/patients": return <svg {...p}><path d="M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" /><circle cx="10" cy="7" r="4" /><path d="M21 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>;
    case "/inbox": return <svg {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>;
    default: return <svg {...p}><circle cx="5" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="19" cy="12" r="1.2" /></svg>;
  }
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const { me, signOut } = useAuth();
  const path = usePathname();
  const live = useLive();
  const [more, setMore] = useState(false);
  const inbox = useQuery({ queryKey: ["inbox-count"], queryFn: () => api<{ open: number }>("/inbox/count"), refetchInterval: live.pollMs });

  const items = NAV.filter((n) => !n.roles || n.roles.includes(me.role));
  const isActive = (href: string) => path === href || path.startsWith(href + "/");
  const secondary = items.filter((n) => !PRIMARY.includes(n.href));
  const badge = (href: string, cls: string) => href === "/inbox" && !!inbox.data?.open
    ? <span className={cls} aria-label={`${inbox.data.open} unhandled`}>{inbox.data.open}</span> : null;

  useEffect(() => { setMore(false); }, [path]);
  useEffect(() => {
    if (!more) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMore(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [more]);

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--surface)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-x-6 px-4 py-2.5 pt-[max(0.625rem,env(safe-area-inset-top))]">
          <Link href="/today" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-teal-600 text-sm font-bold text-white" aria-hidden>S</span>
            <span>ShowUp <span className="hidden font-normal text-[var(--muted)] min-[420px]:inline">· by Neovia AI</span></span>
          </Link>
          {/* desktop / tablet: one row of links */}
          <nav className="hidden gap-1 sm:flex" aria-label="Main">
            {items.map((n) => (
              <Link key={n.href} href={n.href} aria-current={isActive(n.href) ? "page" : undefined}
                className={`relative whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${isActive(n.href) ? "bg-teal-600/15 text-teal-700 dark:text-teal-300" : "text-[var(--muted)] hover:bg-black/5 hover:text-[var(--ink)]"}`}>
                {n.label}
                {badge(n.href, "ml-1.5 inline-flex min-w-[18px] justify-center rounded-full bg-amber-500 px-1 text-[11px] font-bold text-black")}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-[var(--muted)]">
            <span className="inline-flex items-center gap-1.5" title={live.mode === "realtime" ? "Updates arrive instantly" : "Refreshing every 3 seconds"}>
              <span className={`h-2 w-2 rounded-full ${live.mode === "realtime" ? "bg-emerald-500" : "bg-amber-500"}`} />
              {live.mode === "realtime" ? "Live" : "Polling"}
            </span>
            <span className="hidden md:inline">{me.email} · {me.role}</span>
            <button onClick={signOut} className="hidden rounded-lg border border-[var(--line)] px-2.5 py-1 text-[var(--ink)] hover:bg-black/5 sm:block">Sign out</button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 py-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:pb-4">{children}</main>

      {/* phone: bottom tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--line)] bg-[var(--surface)]/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden" aria-label="Primary">
        <ul className="mx-auto grid max-w-lg grid-cols-5">
          {items.filter((n) => PRIMARY.includes(n.href)).map((n) => (
            <li key={n.href}>
              <Link href={n.href} aria-current={isActive(n.href) ? "page" : undefined}
                className={`relative flex h-14 flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${isActive(n.href) ? "text-teal-600 dark:text-teal-300" : "text-[var(--muted)]"}`}>
                <Icon name={n.href} />
                {n.label}
                {badge(n.href, "absolute right-[22%] top-1.5 inline-flex min-w-[16px] justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold leading-4 text-black")}
              </Link>
            </li>
          ))}
          <li>
            <button onClick={() => setMore(true)} aria-haspopup="dialog" aria-expanded={more}
              className={`flex h-14 w-full flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${secondary.some((n) => isActive(n.href)) ? "text-teal-600 dark:text-teal-300" : "text-[var(--muted)]"}`}>
              <Icon name="more" />
              More
            </button>
          </li>
        </ul>
      </nav>

      {more && (
        <div className="fixed inset-0 z-40 sm:hidden" role="dialog" aria-modal="true" aria-label="More">
          <button className="absolute inset-0 bg-black/50" aria-label="Close menu" onClick={() => setMore(false)} />
          <div className="sheet-up absolute inset-x-0 bottom-0 rounded-t-2xl border-t border-[var(--line)] bg-[var(--surface)] p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-[var(--line)]" aria-hidden />
            <ul className="space-y-1">
              {secondary.map((n) => (
                <li key={n.href}>
                  <Link href={n.href} aria-current={isActive(n.href) ? "page" : undefined}
                    className={`flex items-center justify-between rounded-xl px-4 py-3 text-base font-medium ${isActive(n.href) ? "bg-teal-600/15 text-teal-700 dark:text-teal-300" : "hover:bg-black/5"}`}>
                    {n.label}<span aria-hidden className="text-[var(--muted)]">›</span>
                  </Link>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-[var(--line)] pt-3 text-sm">
              <span className="min-w-0 truncate text-[var(--muted)]">{me.email} · {me.role}</span>
              <button onClick={signOut} className="shrink-0 rounded-lg border border-[var(--line)] px-4 py-2 font-medium hover:bg-black/5">Sign out</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
