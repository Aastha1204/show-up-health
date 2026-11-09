"use client";

import Shell from "@/components/Shell";
import { AuthGate } from "@/lib/auth";
import { LiveProvider } from "@/lib/live";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <LiveProvider>
        <Shell>{children}</Shell>
      </LiveProvider>
    </AuthGate>
  );
}
