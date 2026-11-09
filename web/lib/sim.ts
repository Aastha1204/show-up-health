export type Button = { id: string; text: string; payload: string };
export type ListRow = { id: string; title: string; description?: string };
export type ListSpec = { header?: string; button: string; sections: { title?: string; rows: ListRow[] }[] };

export type Thread = {
  kind: "patient" | "doctor";
  id: string;
  name: string;
  phone: string | null;
  is_demo_seed: boolean;
  last_at: string;
  last_body: string;
};

export type Msg = {
  id: string;
  direction: "in" | "out";            // "out" = sent by the clinic (appears on the left of the patient's phone)
  kind: string;
  body: string;
  footer?: string | null;
  buttons?: Button[] | null;
  list?: ListSpec | null;
  status?: "queued" | "sent" | "delivered" | "read" | "failed";
  transport?: "simulator" | "live";
  wa_message_id?: string | null;
  consumed?: boolean;
  error?: string | null;
  created_at: string;
};

export type ThreadMessages = { recipient: { id: string; name: string; phone: string | null }; messages: Msg[] };

const IST = "Asia/Kolkata";

export const istTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { timeZone: IST, hour: "numeric", minute: "2-digit", hour12: true }).toUpperCase();

export const istDay = (iso: string) =>
  new Date(iso).toLocaleDateString("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short" });

export const istDayKey = (iso: string) =>
  new Date(iso).toLocaleDateString("en-CA", { timeZone: IST });
