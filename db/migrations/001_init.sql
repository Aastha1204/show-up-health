-- ShowUp · Neovia AI — core schema (Phase 1)
-- Plain SQL, idempotent-free: run once, in order. All timestamps are timestamptz (UTC).
-- Every table carries clinic_id (denormalised on purpose) so RLS + Realtime filters stay trivial.

create extension if not exists btree_gist;

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- ───────────────────────── clinic ─────────────────────────
create table clinic (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  address text,
  maps_link text,
  parking_note text,
  phone text,
  review_link text,
  timezone text not null default 'Asia/Kolkata',
  avg_ticket_value integer not null default 1200,
  baseline_no_show_rate numeric(5,4) not null default 0.14,
  go_live_date date,
  quiet_hours_start time not null default '21:00',
  quiet_hours_end time not null default '08:00',
  waitlist_offer_ttl_min integer not null default 15 check (waitlist_offer_ttl_min > 0),
  reminder_settings jsonb not null default '{
    "confirmation": {"enabled": true},
    "day_before":   {"enabled": true, "offset_min": 1440},
    "two_hour":     {"enabled": true, "offset_min": 120},
    "post_visit":   {"enabled": true, "offset_min": 30},
    "recall":       {"enabled": true},
    "daily_summary":{"enabled": true, "at": "08:00"}
  }'::jsonb,
  messaging_mode text not null default 'simulator' check (messaging_mode in ('simulator','live')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ───────────────────────── doctor ─────────────────────────
create table doctor (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  name text not null,
  whatsapp_phone text,
  -- {"mon": [["10:00","13:30"],["17:00","21:00"]], "tue": [...], ... "sun": []} in clinic-local time
  working_hours jsonb not null default '{}'::jsonb,
  colour text not null default '#0d9488',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index doctor_clinic_idx on doctor(clinic_id);

-- ───────────────── treatment_type (internal only; never in messages) ─────────────────
create table treatment_type (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  name text not null,
  duration_min integer not null check (duration_min > 0),
  recall_after_days integer check (recall_after_days is null or recall_after_days > 0),
  avg_ticket_value integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index treatment_type_clinic_idx on treatment_type(clinic_id);

-- ───────────────────────── patient ─────────────────────────
create table patient (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  name text not null,
  phone text not null check (phone ~ '^\+[1-9][0-9]{7,14}$'),
  language text not null default 'en' check (language in ('en','hi','mr')),
  consent_source text not null check (consent_source in ('website_form','reception','import','whatsapp')),
  consent_at timestamptz not null default now(),
  opted_out boolean not null default false,
  opted_out_at timestamptz,
  no_show_count integer not null default 0 check (no_show_count >= 0),
  is_demo_seed boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (clinic_id, phone)
);
create index patient_clinic_name_idx on patient(clinic_id, lower(name));

-- ───────────────────────── appointment ─────────────────────────
create table appointment (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  patient_id uuid not null references patient(id) on delete restrict,
  doctor_id uuid not null references doctor(id) on delete restrict,
  treatment_type_id uuid references treatment_type(id) on delete set null,
  start_at timestamptz not null,
  duration_min integer not null check (duration_min > 0),
  end_at timestamptz not null,                      -- maintained by trigger below
  status text not null default 'pending'
    check (status in ('pending','confirmed','rescheduled','cancelled','arrived','no_show')),
  source text not null default 'dashboard'
    check (source in ('dashboard','website','import','waitlist')),
  avg_ticket_value integer not null default 0,
  recovered_via_waitlist boolean not null default false,
  internal_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (end_at > start_at)
);
create index appointment_clinic_start_idx on appointment(clinic_id, start_at);
create index appointment_patient_idx on appointment(patient_id, start_at);
create index appointment_doctor_start_idx on appointment(doctor_id, start_at);

create or replace function appointment_set_end() returns trigger language plpgsql as $$
begin
  new.end_at := new.start_at + make_interval(mins => new.duration_min);
  return new;
end $$;

create trigger appointment_set_end_trg
  before insert or update of start_at, duration_min on appointment
  for each row execute function appointment_set_end();

-- No double booking, guaranteed by the database. A plain unique(doctor, start) would let a
-- 60-min appointment at 10:00 coexist with a 20-min one at 10:30. '[)' lets back-to-back slots touch.
alter table appointment add constraint no_overlap_per_doctor
  exclude using gist (doctor_id with =, tstzrange(start_at, end_at, '[)') with &&)
  where (status in ('pending','confirmed','rescheduled','arrived'));

-- ───────────────────────── appointment_event (status history) ─────────────────────────
create table appointment_event (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  appointment_id uuid not null references appointment(id) on delete cascade,
  from_status text,
  to_status text not null,
  actor text not null check (actor in ('patient','reception','system')),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index appointment_event_appt_idx on appointment_event(appointment_id, created_at);

-- ───────────────────────── outbound_message / message_event ─────────────────────────
create table outbound_message (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  patient_id uuid references patient(id) on delete cascade,
  doctor_id uuid references doctor(id) on delete cascade,
  appointment_id uuid references appointment(id) on delete set null,
  kind text not null check (kind in ('template','interactive_list','interactive_buttons','text')),
  template_name text,
  language text not null default 'en',
  rendered_body text not null,                      -- exactly what the phone shows
  buttons jsonb,
  list jsonb,
  transport text not null check (transport in ('live','simulator')),
  wa_message_id text,
  status text not null default 'queued' check (status in ('queued','sent','delivered','read','failed')),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (num_nonnulls(patient_id, doctor_id) = 1)
);
create index outbound_message_patient_idx on outbound_message(patient_id, created_at);
create index outbound_message_appt_idx on outbound_message(appointment_id, created_at);
create unique index outbound_message_wamid_idx on outbound_message(wa_message_id) where wa_message_id is not null;

create table message_event (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  wa_message_id text,
  event text not null check (event in ('sent','delivered','read','failed','button','list_reply','text')),
  payload jsonb not null default '{}'::jsonb,
  dedupe_key text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index message_event_wamid_idx on message_event(wa_message_id);

-- ───────────────────────── reminder_job ─────────────────────────
create table reminder_job (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  appointment_id uuid references appointment(id) on delete cascade,
  patient_id uuid not null references patient(id) on delete cascade,
  doctor_id uuid references doctor(id) on delete cascade,
  type text not null check (type in ('confirmation','day_before','two_hour','post_visit','recall','daily_summary')),
  due_at timestamptz not null,
  status text not null default 'scheduled' check (status in ('scheduled','sent','skipped','failed','cancelled')),
  skip_reason text,
  sent_at timestamptz,
  outbound_message_id uuid references outbound_message(id) on delete set null,
  attempts integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index reminder_job_due_idx on reminder_job(due_at) where status = 'scheduled';
create index reminder_job_appt_idx on reminder_job(appointment_id);

-- ───────────────────────── waitlist ─────────────────────────
create table waitlist_entry (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  patient_id uuid not null references patient(id) on delete cascade,
  doctor_id uuid references doctor(id) on delete cascade,        -- null = any doctor
  treatment_type_id uuid references treatment_type(id) on delete set null,
  preferred_date date not null,
  preferred_window text not null default 'any' check (preferred_window in ('morning','afternoon','evening','any')),
  status text not null default 'waiting' check (status in ('waiting','offered','booked','removed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index waitlist_entry_date_idx on waitlist_entry(clinic_id, preferred_date, created_at) where status = 'waiting';

create table waitlist_offer (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  waitlist_entry_id uuid not null references waitlist_entry(id) on delete cascade,
  doctor_id uuid not null references doctor(id) on delete cascade,
  slot_start_at timestamptz not null,
  duration_min integer not null check (duration_min > 0),
  status text not null default 'pending' check (status in ('pending','accepted','declined','expired','slot_taken')),
  expires_at timestamptz not null,
  responded_at timestamptz,
  outbound_message_id uuid references outbound_message(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- Only one pending offer per freed slot.
create unique index waitlist_offer_one_pending_per_slot
  on waitlist_offer(doctor_id, slot_start_at) where status = 'pending';

-- ───────────────────────── inbox / broadcast / staff ─────────────────────────
create table inbox_item (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  patient_id uuid not null references patient(id) on delete cascade,
  text text not null,
  received_at timestamptz not null default now(),
  handled boolean not null default false,
  handled_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table broadcast (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  doctor_id uuid not null references doctor(id) on delete cascade,
  delay_min integer not null check (delay_min > 0),
  recipient_count integer not null default 0,
  created_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- user_id is a Supabase auth.users id. No FK: the auth schema does not exist on plain Postgres (tests).
create table staff_profile (
  id uuid primary key default gen_random_uuid(),
  clinic_id uuid not null references clinic(id) on delete cascade,
  user_id uuid not null unique,
  email text,
  role text not null check (role in ('owner','reception')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- updated_at triggers on every table
do $$
declare t text;
begin
  for t in select unnest(array[
    'clinic','doctor','treatment_type','patient','appointment','appointment_event',
    'outbound_message','message_event','reminder_job','waitlist_entry','waitlist_offer',
    'inbox_item','broadcast','staff_profile'])
  loop
    execute format('create trigger %I before update on %I for each row execute function set_updated_at()',
                   t || '_updated_at_trg', t);
  end loop;
end $$;
