-- Phase 8: optional booking deposit via a Razorpay payment link. All columns are null for appointments that never asked for one.
alter table appointment
  add column deposit_status text check (deposit_status in ('requested','paid','expired')),
  add column deposit_amount integer check (deposit_amount > 0),          -- rupees
  add column payment_link_id text,
  add column payment_link_url text,
  add column deposit_paid_at timestamptz;
create unique index appointment_payment_link_idx on appointment(payment_link_id) where payment_link_id is not null;
