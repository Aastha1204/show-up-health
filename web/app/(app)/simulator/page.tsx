"use client";

import SimulatorPane from "@/components/SimulatorPane";

export default function SimulatorPage() {
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-5">
        <h1 className="text-xl font-semibold">WhatsApp Simulator</h1>
        <p className="text-sm text-[var(--muted)]">What the patient sees. Taps here run through the same webhook handler as real WhatsApp.</p>
      </div>
      <SimulatorPane />
    </div>
  );
}
