-- Phase 2: messaging core.

-- Doctors can be demo seeds too: a seeded doctor never receives a real WhatsApp message.
alter table doctor add column is_demo_seed boolean not null default false;

-- Daily summaries go to a doctor, not a patient.
alter table reminder_job alter column patient_id drop not null;
alter table reminder_job add constraint reminder_job_target_chk check (patient_id is not null or doctor_id is not null);
-- One summary per doctor per morning, even if two ticks race to create it.
create unique index reminder_job_daily_summary_uq on reminder_job(doctor_id, due_at) where type = 'daily_summary';

-- Template footer is part of what the phone shows.
alter table outbound_message add column footer text;

-- Inbound events are looked up by sender phone (24-hour customer-service window check).
create index message_event_inbound_from_idx on message_event ((payload->>'from'), created_at)
  where event in ('button','list_reply','text');
