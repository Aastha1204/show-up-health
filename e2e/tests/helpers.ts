import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { buttonTap, textMessage } from "../../web/lib/meta";

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";

export type ApptRow = {
  id: string; patient_id: string; patient_name: string; patient_phone: string; doctor_id: string; start_at: string;
  status: string; internal_note: string | null; treatment_type_id: string | null; patient_language?: string;
};

/** Signed-in API client (the same local dev login the UI uses). */
export async function apiAs(request: APIRequestContext, role: "reception" | "owner" = "reception") {
  const r = await request.post(`${API}/auth/dev-login`, { data: { role } });
  expect(r.ok(), "dev-login (is DEMO_MODE on and SUPABASE_URL empty?)").toBeTruthy();
  const token = (await r.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };
  return {
    token,
    get: async <T>(p: string) => (await (await request.get(`${API}${p}`, { headers })).json()) as T,
    post: async (p: string, data?: unknown) => request.post(`${API}${p}`, { headers, data }),
    put: async (p: string, data?: unknown) => request.put(`${API}${p}`, { headers, data }),
    /** what a patient does on their phone: a Meta-shaped webhook payload through the Simulator endpoint */
    tap: async (phone: string, payload: string, text: string) => request.post(`${API}/simulator/inbound`, { headers, data: buttonTap(phone, payload, text) }),
    say: async (phone: string, text: string) => request.post(`${API}/simulator/inbound`, { headers, data: textMessage(phone, text) }),
  };
}

export const istToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
export const addDays = (d: string, n: number) => { const [y, m, day] = d.split("-").map(Number); return new Date(Date.UTC(y, m - 1, day + n)).toISOString().slice(0, 10); };

export async function signIn(page: Page, role: "reception" | "owner" = "reception") {
  await page.goto("/login");
  await page.getByRole("button", { name: `Sign in as ${role}` }).click();
  await expect(page).toHaveURL(/\/today/);
}

/** Open the schedule on a given IST date (the date box is what a receptionist would use). */
export async function openDay(page: Page, date: string) {
  await page.goto("/today");
  await page.getByLabel("Date", { exact: true }).fill(date);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

/** First upcoming appointment with the given status within the next few days, with the date it is on. */
export async function findUpcoming(api: Awaited<ReturnType<typeof apiAs>>, status: string, skip: string[] = [], language = "en") {
  for (let i = 0; i < 4; i++) {
    const d = addDays(istToday(), i);
    const rows = await api.get<ApptRow[]>(`/appointments?date=${d}`);
    const hit = rows.find((a) => a.status === status && !skip.includes(a.id) && !a.internal_note?.startsWith("DEMO:")
      && (a.patient_language ?? "en") === language
      && new Date(a.start_at).getTime() > Date.now() + 3 * 3600_000);
    if (hit) return { appt: hit, date: d };
  }
  throw new Error(`no upcoming ${status} appointment for a ${language} patient in the seed`);
}

/** The seeded appointment meant to be cancelled live in the demo (its slot matches waitlist entry #1). */
export async function findCancelMe(api: Awaited<ReturnType<typeof apiAs>>) {
  for (let i = 0; i < 4; i++) {
    const d = addDays(istToday(), i);
    const hit = (await api.get<ApptRow[]>(`/appointments?date=${d}`)).find((a) => a.internal_note?.startsWith("DEMO: cancel me"));
    if (hit) return { appt: hit, date: d };
  }
  throw new Error("seed has no cancel-me appointment");
}

/** Simulate 25 hours passing since the patient last wrote to us (their 24-hour WhatsApp window closes). */
export function closeWindow(phone: string) {
  const python = process.env.E2E_PYTHON ?? "python";
  const db = process.env.E2E_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54329/showup";
  execFileSync(python, ["-c",
    "import sys, psycopg; c = psycopg.connect(sys.argv[1], autocommit=True); "
    + "c.execute(\"update message_event set created_at = now() - interval '25 hours' where payload->>'from' = %s\", [sys.argv[2]])",
    db, phone]);
}

const pyCmd = (code: string, ...args: string[]) => {
  const python = process.env.E2E_PYTHON ?? "python";
  const db = process.env.E2E_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:54329/showup";
  execFileSync(python, ["-c", code, db, ...args]);
};

/** Waitlist offers are proactive messages, so they obey quiet hours (21:00-08:00 IST). A demo run at night would
 *  correctly send none, so the end-to-end demo test switches quiet hours off first (a re-seed restores the defaults). */
export function disableQuietHours() {
  pyCmd("import sys, psycopg; c = psycopg.connect(sys.argv[1], autocommit=True); "
        + "c.execute(\"update clinic set quiet_hours_start='00:00', quiet_hours_end='00:00'\")");
}

/** First day in the next week that has bookable public slots, plus the ids the public form needs. */
export async function firstBookableDate(request: APIRequestContext) {
  const clinic = await (await request.get(`${API}/public/clinic`)).json();
  const tt = clinic.treatments.find((t: { name: string }) => t.name === "Consultation") ?? clinic.treatments[0];
  for (let i = 1; i <= 8; i++) {
    const d = addDays(istToday(), i);
    const r = await (await request.get(`${API}/public/slots?date=${d}&treatment_type_id=${tt.id}`)).json();
    if (r.slots.length) return { date: d, treatmentId: tt.id as string, slots: r.slots as string[] };
  }
  throw new Error("no bookable public slots in the next 8 days");
}

/** "HH:MM" in IST for an ISO instant. */
export const istHHMM = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export const uniquePhone = () => "9" + String(Math.floor(Math.random() * 1e9)).padStart(9, "0");
