-- Row-level security + Realtime publication.
-- Supabase-only: on plain Postgres (CI / local tests) there is no `auth` schema, so this is a no-op.
-- All writes go through FastAPI with the service-role key (bypasses RLS); staff get SELECT only.

do $$
declare
  t text;
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    raise notice 'No auth schema found - skipping RLS/Realtime setup (non-Supabase database).';
    return;
  end if;

  execute $f$
    create or replace function public.current_clinic_id() returns uuid
    language sql stable security definer set search_path = public as
    $b$ select clinic_id from public.staff_profile where user_id = auth.uid() $b$
  $f$;

  for t in select unnest(array[
    'doctor','treatment_type','patient','appointment','appointment_event','outbound_message',
    'message_event','reminder_job','waitlist_entry','waitlist_offer','inbox_item','broadcast','staff_profile'])
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy %I on public.%I for select to authenticated using (clinic_id = public.current_clinic_id())',
      t || '_staff_select', t);
  end loop;

  execute 'alter table public.clinic enable row level security';
  execute 'create policy clinic_staff_select on public.clinic for select to authenticated using (id = public.current_clinic_id())';

  -- Realtime: the UI invalidates its queries when these change.
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    for t in select unnest(array['appointment','outbound_message','message_event','waitlist_offer','inbox_item'])
    loop
      execute format('alter publication supabase_realtime add table public.%I', t);
    end loop;
  end if;
end $$;
