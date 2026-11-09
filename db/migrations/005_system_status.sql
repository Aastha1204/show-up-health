-- Phase 7: small key/value store for operational facts the Settings health check shows (e.g. when Meta last called us).
create table system_status (
  key text primary key,
  value jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
