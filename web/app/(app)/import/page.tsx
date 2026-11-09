"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useToast } from "@/components/Toasts";
import { btnGhost, btnPrimary, inputCls } from "@/components/ui";
import { ApiError, post } from "@/lib/api";
import { fmtDay, fmtTime, istDate } from "@/lib/time";

type Field = { key: string; label: string; required: boolean };
type Row = { row: number; ok: boolean; errors: string[]; warnings: string[]; raw: Record<string, string>;
  summary: { patient: string; phone: string; doctor: string; treatment: string; start_at: string; language: string } | null };
type Preview = { csv_text: string; headers: string[]; mapping: Record<string, string | null>; fields: Field[]; total_rows: number;
  needs_mapping: boolean; rows: Row[]; counts: { total: number; ok: number; errors: number; warnings: number } };
type Result = { created: number; skipped: { row: number; reason: string }[]; counts: Preview["counts"] };

const MAX_BYTES = 2 * 1024 * 1024;
const TEMPLATE = "Patient,Mobile,Doctor,Date,Time,Service,Language,Notes\nAsha Rao,98765 43210,Dr. Neha Kulkarni,2026-11-16,10:30,Consultation,English,\n";

export default function ImportPage() {
  const toast = useToast();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<"file" | "sheet">("file");
  const [sheetUrl, setSheetUrl] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);        // the CSV text of an uploaded file
  const [pv, setPv] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [consent, setConsent] = useState(false);
  const [sendConf, setSendConf] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const msg = (e: unknown) => (e instanceof ApiError ? e.message : (e as Error).message);
  // Quick successive changes must not lose each other (mapRef is always current), and a slow OLD preview must never
  // overwrite a newer one (only the latest request's answer is applied).
  const mapRef = useRef<Record<string, string | null>>({});
  const seq = useRef(0);
  const preview = useMutation({
    mutationFn: ({ seq: _s, ...b }: { seq: number; csv_text?: string; sheet_url?: string; mapping?: Record<string, string | null> }) => post<Preview>("/import/preview", b),
    onSuccess: (r, v) => { if (v.seq !== seq.current) return; mapRef.current = r.mapping; setPv(r); setMapping(r.mapping); setErr(null); },
    onError: (e, v) => { if (v.seq !== seq.current) return; setErr(msg(e)); setPv(null); },
  });
  const runPreview = (b: { csv_text?: string; sheet_url?: string; mapping?: Record<string, string | null> }) => preview.mutate({ ...b, seq: ++seq.current });
  const commit = useMutation({
    mutationFn: () => post<Result>("/import/commit", { csv_text: pv!.csv_text, mapping, consent_confirmed: consent, send_confirmations: sendConf }),
    onSuccess: (r) => { setResult(r); toast(`${r.created} appointment${r.created === 1 ? "" : "s"} imported`, "success"); qc.invalidateQueries(); },
    onError: (e) => setErr(msg(e)),
  });

  function onFile(f: File | undefined) {
    setErr(null); setPv(null); setResult(null);
    if (!f) return;
    if (f.size > MAX_BYTES) return setErr("That file is larger than 2 MB.");
    setFileName(f.name);
    const rd = new FileReader();
    rd.onload = () => { const t = String(rd.result ?? ""); setText(t); mapRef.current = {}; runPreview({ csv_text: t }); };
    rd.onerror = () => setErr("Could not read that file.");
    rd.readAsText(f);
  }
  const reset = () => { setPv(null); setText(null); setFileName(null); setResult(null); setErr(null); setConsent(false); setSendConf(false); setSheetUrl(""); if (fileRef.current) fileRef.current.value = ""; };
  const remap = (key: string, col: string) => { const m = { ...mapRef.current, [key]: col || null }; mapRef.current = m; setMapping(m); runPreview({ csv_text: pv!.csv_text, mapping: m }); };

  if (result) {
    return (
      <div className="mx-auto max-w-3xl" data-testid="import-result">
        <h1 className="text-xl font-semibold">Import finished</h1>
        <div className="mt-4 rounded-xl border border-teal-600/40 bg-teal-600/10 p-5">
          <p className="text-lg font-semibold">{result.created} appointment{result.created === 1 ? "" : "s"} imported</p>
          <p className="mt-1 text-sm text-[var(--muted)]">{result.skipped.length} row{result.skipped.length === 1 ? "" : "s"} skipped. Reminders are scheduled; {sendConf ? "a booking confirmation is on its way to each patient" : "no confirmation message was sent"}.</p>
        </div>
        {result.skipped.length > 0 && (
          <details className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4" open>
            <summary className="cursor-pointer text-sm font-semibold">Skipped rows</summary>
            <ul className="mt-2 space-y-1 text-sm">{result.skipped.map((s) => <li key={s.row}><b>Row {s.row}:</b> <span className="text-[var(--muted)]">{s.reason}</span></li>)}</ul>
          </details>
        )}
        <div className="mt-4 flex gap-2"><button className={btnPrimary} onClick={reset}>Import another file</button></div>
      </div>
    );
  }

  const shown = pv ? (onlyProblems ? pv.rows.filter((r) => !r.ok || r.warnings.length) : pv.rows) : [];
  const canImport = !!pv && !pv.needs_mapping && pv.counts.ok > 0 && consent && !commit.isPending;

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="text-xl font-semibold">Import appointments</h1>
      <p className="mb-4 text-xs text-[var(--muted)]">From a CSV file or a Google Sheet. Nothing is saved until you press Import; rows that already exist are skipped, so re-importing a file is safe.</p>

      <section className="rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4" aria-label="Choose a source">
        <div className="mb-3 flex gap-1 rounded-lg border border-[var(--line)] p-0.5 text-sm" role="tablist" style={{ width: "fit-content" }}>
          {(["file", "sheet"] as const).map((s) => <button key={s} role="tab" aria-selected={source === s} onClick={() => { setSource(s); reset(); }} className={`rounded-md px-3 py-1.5 font-medium ${source === s ? "bg-teal-600 text-white" : "text-[var(--muted)]"}`}>{s === "file" ? "CSV file" : "Google Sheet link"}</button>)}
        </div>
        {source === "file" ? (
          <div className="flex flex-wrap items-center gap-3">
            <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" aria-label="CSV file" onChange={(e) => onFile(e.target.files?.[0])} className="text-sm" />
            <a className="text-sm text-teal-700 underline dark:text-teal-300" href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`} download="showup-import-template.csv">Download a template</a>
            {fileName && <span className="text-xs text-[var(--muted)]">{fileName}</span>}
          </div>
        ) : (
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setErr(null); setResult(null); mapRef.current = {}; runPreview({ sheet_url: sheetUrl.trim() }); }}>
            <input value={sheetUrl} onChange={(e) => setSheetUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" aria-label="Google Sheet link" className={`${inputCls} min-w-[280px] flex-1`} />
            <button className={btnPrimary} disabled={!sheetUrl.trim() || preview.isPending}>Load sheet</button>
            <p className="w-full text-xs text-[var(--muted)]">Use File → Share → Publish to web → CSV, or share the sheet so anyone with the link can view it. Only Google Sheets links are accepted.</p>
          </form>
        )}
        {preview.isPending && <p className="mt-3 text-sm text-[var(--muted)]" role="status">Checking every row…</p>}
        {err && <p role="alert" className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-300">{err}</p>}
      </section>

      {pv && (
        <>
          <section className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-4" aria-label="Match your columns">
            <h2 className="text-sm font-semibold">Match your columns</h2>
            <p className="mb-3 text-xs text-[var(--muted)]">We guessed from your headers. Fix anything that is wrong.</p>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {pv.fields.map((f) => (
                <label key={f.key} className="block text-sm">
                  <span className="mb-1 block font-medium">{f.label}{f.required && <span className="text-red-600"> *</span>}</span>
                  <select value={mapping[f.key] ?? ""} onChange={(e) => remap(f.key, e.target.value)} className={inputCls}>
                    <option value="">{f.required ? "Choose a column…" : "(none)"}</option>
                    {pv.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                  </select>
                </label>
              ))}
            </div>
            {pv.needs_mapping && <p role="status" className="mt-3 text-sm text-amber-700 dark:text-amber-300">Choose a column for every required field to see the preview.</p>}
          </section>

          {!pv.needs_mapping && (
            <section className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--surface)]" aria-label="Preview">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] p-4">
                <div className="flex flex-wrap gap-4 text-sm" data-testid="import-counts">
                  <span><b className="text-lg">{pv.counts.total}</b> rows</span>
                  <span className="text-emerald-700 dark:text-emerald-300"><b className="text-lg">{pv.counts.ok}</b> ready</span>
                  <span className="text-red-700 dark:text-red-300"><b className="text-lg">{pv.counts.errors}</b> with problems</span>
                  {pv.counts.warnings > 0 && <span className="text-amber-700 dark:text-amber-300"><b className="text-lg">{pv.counts.warnings}</b> with warnings</span>}
                </div>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} className="accent-teal-600" /> Show only problems</label>
              </div>
              <div className="max-h-[420px] overflow-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="sticky top-0 bg-[var(--surface)] text-left text-xs uppercase tracking-wide text-[var(--muted)]"><tr><th className="px-4 py-2">Row</th><th>Status</th><th>Patient</th><th>Phone</th><th>Doctor</th><th>When</th><th className="pr-4">Notes</th></tr></thead>
                  <tbody className="divide-y divide-[var(--line)]">
                    {shown.map((r) => (
                      <tr key={r.row} data-row={r.row} data-ok={r.ok}>
                        <td className="px-4 py-2 tabular-nums text-[var(--muted)]">{r.row}</td>
                        <td>{r.ok ? (r.warnings.length ? <span className="font-semibold text-amber-700 dark:text-amber-300">⚠ Warning</span> : <span className="font-semibold text-emerald-700 dark:text-emerald-300">✓ Ready</span>) : <span className="font-semibold text-red-700 dark:text-red-300">✕ Problem</span>}</td>
                        <td>{r.summary?.patient ?? Object.values(r.raw)[0]}</td>
                        <td className="tabular-nums">{r.summary?.phone ?? ""}</td>
                        <td>{r.summary ? `${r.summary.doctor}` : ""}</td>
                        <td>{r.summary ? `${fmtDay(istDate(r.summary.start_at))}, ${fmtTime(r.summary.start_at)}` : ""}</td>
                        <td className="pr-4 text-xs">{[...r.errors.map((e) => <div key={e} className="text-red-700 dark:text-red-300">{e}</div>), ...r.warnings.map((w) => <div key={w} className="text-amber-700 dark:text-amber-300">{w}</div>)]}</td>
                      </tr>
                    ))}
                    {shown.length === 0 && <tr><td colSpan={7} className="p-6 text-center text-[var(--muted)]">No problems found.</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="space-y-3 border-t border-[var(--line)] p-4">
                <label className="flex items-start gap-2.5 text-sm"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5 h-4 w-4 accent-teal-600" />
                  <span>I confirm these patients agreed to receive WhatsApp appointment updates. <span className="text-[var(--muted)]">(Recorded as “imported list”.)</span></span></label>
                <label className="flex items-start gap-2.5 text-sm"><input type="checkbox" checked={sendConf} onChange={(e) => setSendConf(e.target.checked)} className="mt-0.5 h-4 w-4 accent-teal-600" />
                  <span>Also send each patient a booking confirmation now <span className="text-[var(--muted)]">(off by default: reminders are scheduled either way, but a confirmation to every imported patient at once is rarely wanted).</span></span></label>
                <div className="flex gap-2">
                  <button className={btnPrimary} disabled={!canImport} onClick={() => commit.mutate()}>{commit.isPending ? "Importing…" : `Import ${pv.counts.ok} appointment${pv.counts.ok === 1 ? "" : "s"}`}</button>
                  <button className={btnGhost} onClick={reset}>Start over</button>
                </div>
                {pv.counts.errors > 0 && <p className="text-xs text-[var(--muted)]">Rows with problems are skipped; fix them in your file and import again.</p>}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
