-- Phase 3: a patient's tap and the replies it triggers are written in ONE transaction, and now() is frozen for the
-- whole transaction, so they would share a timestamp and sort arbitrarily in the chat and message log.
-- clock_timestamp() gives each row its real insert time (microsecond resolution), which keeps conversations in order.
alter table outbound_message  alter column created_at set default clock_timestamp();
alter table message_event     alter column created_at set default clock_timestamp();
alter table appointment_event alter column created_at set default clock_timestamp();
alter table inbox_item        alter column created_at set default clock_timestamp();
