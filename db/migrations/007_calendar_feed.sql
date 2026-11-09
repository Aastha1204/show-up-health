-- Phase 8: a private, per-doctor calendar feed (iCalendar). The token IS the credential: anyone holding the URL can read
-- that doctor's schedule, so it is long, random, and rotatable from Settings.
alter table doctor add column calendar_token text not null
  default (replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', ''));
create unique index doctor_calendar_token_idx on doctor(calendar_token);
