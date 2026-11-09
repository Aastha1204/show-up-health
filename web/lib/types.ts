export type Status = "pending" | "confirmed" | "rescheduled" | "cancelled" | "arrived" | "no_show";
export type Source = "dashboard" | "website" | "import" | "waitlist";
export type Language = "en" | "hi" | "mr";
export type ConsentSource = "website_form" | "reception" | "import" | "whatsapp";

export type Doctor = { id: string; name: string; colour: string; working_hours: Record<string, [string, string][]> };
export type Treatment = { id: string; name: string; duration_min: number; recall_after_days: number | null; avg_ticket_value: number };
export type Reference = {
  clinic: { id: string; name: string; waitlist_offer_ttl_min: number; messaging_mode: string };
  doctors: Doctor[];
  treatment_types: Treatment[];
};

export type Appt = {
  id: string; patient_id: string; doctor_id: string; treatment_type_id: string | null;
  start_at: string; end_at: string; duration_min: number; status: Status; source: Source;
  recovered_via_waitlist: boolean; internal_note: string | null;
  deposit_status?: "requested" | "paid" | "expired" | null; deposit_amount?: number | null; payment_link_url?: string | null;
  patient_name: string; patient_phone: string; patient_language: Language; patient_opted_out: boolean;
  patient_no_show_count: number; patient_is_demo_seed: boolean;
  doctor_name: string; doctor_colour: string; treatment_name: string | null;
  last_event_status: Status | null; last_event_actor: "patient" | "reception" | "system" | null; last_event_at: string | null;
};
export type ApptDetail = Appt & { events: { from_status: Status | null; to_status: Status; actor: string; detail: Record<string, unknown>; created_at: string }[] };

export type Patient = {
  id: string; name: string; phone: string; language: Language; consent_source: ConsentSource; consent_at: string;
  opted_out: boolean; opted_out_at: string | null; no_show_count: number; is_demo_seed: boolean;
};

export type WaitlistEntry = {
  id: string; patient_id: string; patient_name: string; patient_phone: string; doctor_id: string | null; doctor_name: string | null;
  treatment_type_id: string | null; treatment_name: string | null; preferred_date: string;
  preferred_window: "morning" | "afternoon" | "evening" | "any"; status: "waiting" | "offered" | "booked" | "removed"; created_at: string;
  offer_id: string | null; offer_slot_start_at: string | null; offer_expires_at: string | null; offer_doctor_id: string | null;
};

export type InboxItem = {
  id: string; patient_id: string; patient_name: string; patient_phone: string; patient_opted_out: boolean;
  text: string; received_at: string; handled: boolean; handled_by: string | null; window_open: boolean;
};

export type LogItem =
  | { type: "message"; at: string; id: string; kind: string; template_name: string | null; body: string; footer: string | null;
      transport: "simulator" | "live"; status: string; error: string | null; buttons: { text: string }[] | null;
      list: unknown | null; ticks: { sent?: string; delivered?: string; read?: string; failed?: string } }
  | { type: "reply"; at: string; id: string; via: string; text: string | null; payload: string | null }
  | { type: "status"; at: string; id: string; from_status: Status | null; to_status: Status; actor: string; detail: Record<string, unknown> };

export type Slots = { slots: string[]; duration_min: number };

export const STATUS_LABEL: Record<Status, string> = {
  pending: "Pending", confirmed: "Confirmed", rescheduled: "Rescheduled", cancelled: "Cancelled", arrived: "Arrived", no_show: "No-show",
};
export const SOURCE_ICON: Record<Source, { icon: string; label: string }> = {
  dashboard: { icon: "⌨", label: "Added by reception" }, website: { icon: "🌐", label: "Booked on the website" },
  import: { icon: "⇪", label: "Imported" }, waitlist: { icon: "⏳", label: "Booked from the waitlist" },
};
export const CONSENT_LABEL: Record<ConsentSource, string> = {
  website_form: "Website form", reception: "Told reception", import: "Imported list", whatsapp: "WhatsApp",
};
export const LANGUAGE_LABEL: Record<Language, string> = { en: "English", hi: "Hindi", mr: "Marathi" };
