"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, post } from "@/lib/api";
import { fmtTime } from "@/lib/time";
import type { Doctor } from "@/lib/types";
import { useToast } from "./Toasts";
import { btnGhost, btnPrimary, Field, inputCls, Modal } from "./ui";

type Preview = { doctor_name: string; delay_min: number; recipient_count: number; preview: string; recipients: { patient_id: string; name: string; start_at: string }[] };
type Result = { targeted: number; sent: number; skipped: Record<string, number>; failed: number };
const DELAYS = [15, 30, 45, 60];

export default function RunningLateModal({ doctors, onClose }: { doctors: Doctor[]; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [doctorId, setDoctorId] = useState(doctors[0]?.id ?? "");
  const [delay, setDelay] = useState(30);
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const pv = useQuery({
    queryKey: ["late-preview", doctorId, delay], enabled: !!doctorId && !result,
    queryFn: () => api<Preview>(`/broadcasts/running-late/preview?doctor_id=${doctorId}&delay_min=${delay}`),
    refetchInterval: 10_000,
  });
  const send = useMutation({
    mutationFn: () => post<Result>("/broadcasts/running-late", { doctor_id: doctorId, delay_min: delay }),
    onSuccess: (r) => { setResult(r); setErr(null); toast(`Running-late message sent to ${r.sent} patient${r.sent === 1 ? "" : "s"}`, "success"); qc.invalidateQueries(); },
    onError: (e) => setErr(e instanceof ApiError && e.status === 409 ? "That message was just sent. Wait a minute before sending it again." : (e as Error).message),
  });

  if (result) {
    const skipped = Object.entries(result.skipped);
    return (
      <Modal title="Running late: sent" onClose={onClose}>
        <p className="text-sm"><b>{result.sent}</b> of {result.targeted} patients were messaged.</p>
        {skipped.length > 0 && <p className="mt-1 text-sm text-[var(--muted)]">Not sent: {skipped.map(([k, n]) => `${n} (${k.replaceAll("_", " ")})`).join(", ")}.</p>}
        {result.failed > 0 && <p className="mt-1 text-sm text-red-600">{result.failed} could not be sent: check the Message log on those appointments.</p>}
        {result.targeted === 0 && <p className="mt-1 text-sm text-[var(--muted)]">Nobody has a remaining appointment today with this doctor.</p>}
        <div className="mt-4 flex justify-end"><button className={btnPrimary} onClick={onClose}>Done</button></div>
      </Modal>
    );
  }

  const count = pv.data?.recipient_count ?? 0;
  return (
    <Modal title="Running late" onClose={onClose}>
      <div className="space-y-4">
        <Field label="Doctor">
          <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)} className={inputCls}>
            {doctors.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        <div>
          <div className="mb-1 text-sm font-medium">How late?</div>
          <div className="flex gap-2" role="radiogroup" aria-label="Delay in minutes">
            {DELAYS.map((m) => (
              <button key={m} type="button" role="radio" aria-checked={delay === m} onClick={() => setDelay(m)}
                className={`flex-1 rounded-lg border py-2 text-sm font-medium ${delay === m ? "border-teal-600 bg-teal-600 text-white" : "border-[var(--line)] hover:bg-black/5"}`}>{m} min</button>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 text-sm font-medium">What patients will receive</div>
          <div className="rounded-lg bg-[#efeae2] p-3">
            <div className="max-w-[92%] rounded-lg bg-white px-3 py-2 text-[13px] leading-snug text-gray-900 shadow-sm">{pv.data?.preview ?? "…"}</div>
          </div>
        </div>

        <div className="rounded-lg border border-[var(--line)] p-3 text-sm">
          <b>{count}</b> {count === 1 ? "patient" : "patients"} will be messaged
          <span className="text-[var(--muted)]"> · remaining appointments today that are pending, confirmed or rescheduled (opted-out patients excluded)</span>
          {count > 0 && (
            <ul className="mt-2 max-h-28 space-y-0.5 overflow-y-auto text-xs text-[var(--muted)]">
              {pv.data!.recipients.map((r) => <li key={r.patient_id}>{fmtTime(r.start_at)} · {r.name}</li>)}
            </ul>
          )}
        </div>
        {err && <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{err}</p>}
        <div className="flex justify-end gap-2">
          <button className={btnGhost} onClick={onClose}>Cancel</button>
          <button className={btnPrimary} disabled={count === 0 || send.isPending || pv.isFetching && !pv.data} onClick={() => send.mutate()}>
            {send.isPending ? "Sending…" : `Send to ${count}`}
          </button>
        </div>
      </div>
    </Modal>
  );
}
