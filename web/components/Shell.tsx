"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
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

export default function Shell({ children }: { children: React.ReactNode }) {
  const { me, signOut } = useAuth();
  const path = usePathname();
  const live = useLive();
  const inbox = useQuery({ queryKey: ["inbox-count"], queryFn: () => api<{ open: number }>("/inbox/count"), refetchInterval: live.pollMs });

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--surface)]/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <Link href="/today" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-teal-600 text-sm font-bold text-white" aria-hidden>S</span>
            <span>ShowUp <span className="font-normal text-[var(--muted)]">· by Neovia AI</span></span>
          </Link>
          <nav className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto" aria-label="Main">
            {NAV.filter((n) => !n.roles || n.roles.includes(me.role)).map((n) => {
              const active = path === n.href || path.startsWith(n.href + "/");
              return (
                <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
                  className={`relative whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${active ? "bg-teal-600/15 text-teal-700 dark:text-teal-300" : "text-[var(--muted)] hover:bg-black/5 hover:text-[var(--ink)]"}`}>
                  {n.label}
                  {n.href === "/inbox" && !!inbox.data?.open && (
                    <span className="ml-1.5 inline-flex min-w-[18px] justify-center rounded-full bg-amber-500 px-1 text-[11px] font-bold text-black" aria-label={`${inbox.data.open} unhandled`}>{inbox.data.open}</span>
                  )}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-[var(--muted)]">
            <span className="inline-flex items-center gap-1.5" title={live.mode === "realtime" ? "Updates arrive instantly" : "Refreshing every 3 seconds"}>
              <span className={`h-2 w-2 rounded-full ${live.mode === "realtime" ? "bg-emerald-500" : "bg-amber-500"}`} />
              {live.mode === "realtime" ? "Live" : "Polling"}
            </span>
            <span className="hidden sm:inline">{me.email} · {me.role}</span>
            <button onClick={signOut} className="rounded-lg border border-[var(--line)] px-2.5 py-1 text-[var(--ink)] hover:bg-black/5">Sign out</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-4 py-4">{children}</main>
    </div>
  );
}
