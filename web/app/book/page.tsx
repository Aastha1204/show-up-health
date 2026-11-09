"use client";

import { useEffect, useState } from "react";
import BookingForm from "@/components/BookingForm";

function Snippet({ title, code, note }: { title: string; code: string; note?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between"><h3 className="text-sm font-semibold">{title}</h3>
        <button className="rounded-md border border-[var(--line)] px-2 py-0.5 text-xs hover:bg-black/5"
          onClick={() => { navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {}); }}>{copied ? "Copied" : "Copy"}</button></div>
      <pre className="overflow-x-auto rounded-lg border border-[var(--line)] bg-black/5 p-3 text-xs leading-relaxed"><code>{code}</code></pre>
      {note && <p className="mt-1 text-xs text-[var(--muted)]">{note}</p>}
    </div>
  );
}

export default function BookPage() {
  const [origin, setOrigin] = useState("https://YOUR-SHOWUP-HOST");
  useEffect(() => setOrigin(window.location.origin), []);
  const iframe = `<iframe src="${origin}/book/embed" title="Book an appointment" width="100%" height="820"\n        style="border:0;max-width:560px" loading="lazy"></iframe>`;
  const api = `fetch("https://YOUR-SHOWUP-API-HOST/public/appointments", {\n  method: "POST", headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({ name, phone: "+919876543210", language: "en", doctor_id: null,\n    treatment_type_id, start_at, consent: true })   // consent must be true; website must stay empty\n})`;
  return (
    <main className="mx-auto max-w-xl px-4 py-8">
      <header className="mb-6 text-center">
        <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-teal-600 text-xl font-bold text-white" aria-hidden>S</div>
        <h1 className="text-2xl font-semibold">Book an appointment</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">SmileCare Dental Clinic · Kalyan West</p>
      </header>
      <div className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-sm"><BookingForm /></div>

      <section className="mt-10 space-y-4 border-t border-[var(--line)] pt-6" aria-label="For the clinic's website">
        <h2 className="text-base font-semibold">For the clinic&apos;s website</h2>
        <p className="text-sm text-[var(--muted)]">This page stands in for the booking form on the clinic&apos;s own site. Paste this where the form should appear and bookings land on the reception schedule.</p>
        <Snippet title="Embed the form" code={iframe} note="The form page can be framed by any site. Set NEXT_PUBLIC_EMBED_ORIGINS to restrict it to the clinic's domain." />
        <Snippet title="Or post from the site's own form" code={api}
          note="The clinic's domain must be listed in PUBLIC_BOOKING_ALLOWED_ORIGINS on the API. Requests are rate-limited, and a hidden honeypot field must stay empty." />
      </section>
    </main>
  );
}
