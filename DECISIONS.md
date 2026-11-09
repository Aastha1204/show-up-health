# Decisions

Where the brief was silent, the simplest option that keeps the demo reliable. Newest at the bottom.

## Phase 1
- **Blank [FILL IN] lines.** No existing dental website URL and no Neovia Core location were given, so ShowUp is
  standalone and `/book` (Phase 6) is the stand-in booking form. Revisit if either is supplied.
- **psycopg 3 (async) with raw SQL, not SQLAlchemy.** The interesting logic is Postgres-specific (exclusion
  constraint, `FOR UPDATE SKIP LOCKED`); an ORM adds indirection without benefit here.
- **`clinic_id` on every table**, including child tables the brief lists without it. Keeps RLS policies and Realtime
  filters one-liners. The brief did not forbid extra columns.
- **Enums are `text` + CHECK constraints**, not Postgres enums — easier to evolve with plain SQL migrations.
- **`staff_profile.user_id` has no FK to `auth.users`.** The `auth` schema does not exist on plain Postgres (tests,
  docker). RLS/Realtime migration `002` is a no-op without it.
- **`end_at` is maintained by a trigger** (not the app), so no code path can write an inconsistent range.
- **Working-hours enforcement lives in the service layer** (`is_slot_valid`), reachable only from the API with
  `enforce_hours=True`; the seed and tests bypass it explicitly. Overlap is enforced by the database regardless.
- **Slot grid is 15 minutes in IST**; a slot is valid only if the whole visit fits inside one working range, so
  nothing straddles the 13:30–17:00 break. A 20-minute visit therefore ends at 13:00 / 20:30 at the latest.
- **Status machine:** `arrived`, `cancelled`, `no_show` are terminal. `rescheduled` is entered only by moving an
  appointment (time or doctor), never by a bare status call. Repeating the current status is a no-op with no
  duplicate history event. Only pending/confirmed/rescheduled appointments can be edited.
- **No-show rate = no_show / (arrived + no_show).** Cancellations are excluded from the denominator.
- **Waitlist time windows** (used from Phase 4): morning < 14:00, afternoon 14:00–17:00, evening ≥ 17:00. The clinic
  has no afternoon hours, so "afternoon" entries only match nothing — intentional.
- **Sunday closure.** The brief's hours are Mon–Sat, so on a Sunday there is no "today". The seed's live window is
  today plus the next two calendar days, working days only. Consequence: seeded on a Sunday or after 21:00 there is
  nothing in the next 12 hours, so the "Call these patients" panel is legitimately empty until you Reset demo on a
  working day. (The seed asserts ≥3 pending in the next 12 h *when* appointments exist in that window.)
  The `/demo` "tomorrow" booking (Phase 6) must therefore mean "next working day".
- **Seed numbers** are exact per 7-day block (not probabilistic) so the weekly chart is stable: ~14.1% no-show
  before go-live, ~6.1% after, exactly 18 recovered slots, 20–24 appointments per working day, 500 patients. The seed
  asserts these on every generation. Past appointments get no `appointment_event` rows (only today/tomorrow do).
- **Seeded phone numbers** are `+9190xxxxxxxx` and fictional-but-possibly-real-looking; harmless because
  `is_demo_seed` forces the Simulator transport (enforced in the transport layer in Phase 2).
- **Reset demo preserves `staff_profile` rows**, re-pointing them at the new clinic id, so logins survive a reset.
- **Test DB requirement.** DB tests need Postgres with `btree_gist` (the standard `postgres:16` image has it); the
  pip-bundled Postgres wheels do not. They skip automatically without `TEST_DATABASE_URL`.

## Phase 2
- **Phase boundary.** The webhook parses, dedupes and *records* every inbound event (button, list reply, button reply,
  text) but triggers no business action yet; `messaging/inbound.py::dispatch` is the Phase 3 hook. STOP/opt-out, Confirm,
  Cancel, Reschedule and free-text-to-inbox are therefore not live. (The opt-out *guard* on sends is.)
- **LiveTransport is a deliberate stub** that fails loudly with `live_unavailable` (Phase 7). Seeded patients and
  doctors are forced onto the Simulator in `choose_transport` *and* `LiveTransport` refuses seeds (defence in depth).
  Added `doctor.is_demo_seed` (migration 003) for the same reason on the 8 a.m. summary.
- **Ports.** Web dev port is **3100** (3000 is commonly taken, e.g. Grafana here). The browser only calls `/api/*`,
  which Next proxies to FastAPI, so there is no CORS and no API URL in the bundle.
- **Local dev auth.** `/auth/dev-login` mints a token for a seeded demo user, only when `DEMO_MODE=true`, a local JWT
  secret is set and `SUPABASE_URL` is empty. Real Supabase login arrives with the reception UI (Phase 5).
- **Windows event loop.** psycopg async cannot use the Proactor loop; `api/run.py` sets the selector policy before
  uvicorn creates its loop. (Do not edit `.env`/JSON with PowerShell 5.1 `Set-Content -Encoding utf8`: it adds a BOM.)
- **Medical-content guard.** Whole-word matching for English (so "painting", "refilling" pass); substring matching for
  Hindi/Marathi (heavily inflected: दर्द, दर्दनाक). Checks body, footer, button labels and list rows against every
  clinic treatment name plus the blocklist. The clinic *name* ("SmileCare Dental Clinic") is allowed: it is the
  business name, and "dental" is not on the blocklist. **The Hindi/Marathi blocklist needs native-speaker review.**
  A blocked job is marked `failed` / `medical_content`, logged at ERROR, never retried, and creates no outbound row.
- **Quiet hours.** Reminder jobs are shifted to the moment quiet hours began (21:00), or to the next 08:00 if that has
  already passed. Direct proactive sends inside quiet hours are *skipped* (`quiet_hours`), not deferred. A night-time
  booking's confirmation waits for 08:00.
- **day_before rules as built.** Due `start-24h`; if that is already past and the visit is >3 h away, due 5 min after the
  confirmation; otherwise none. Skipped at execution if already confirmed. `two_hour` skipped if already past.
  Moving an appointment cancels its scheduled jobs and creates fresh ones, including a new confirmation.
- **Tick.** Claims ONE job per transaction with `FOR UPDATE SKIP LOCKED`, sends, marks, commits, repeats (max 200).
  A crash mid-batch can therefore never re-send jobs already committed. Transient transport failures retry up to 3
  times (1, 2 min back-off). Expired waitlist offers are only marked `expired`; offering to the next candidate is Phase 4.
- **Daily summary.** Created by the tick: 08:00 IST per active doctor for today (30-min grace) and tomorrow; a unique
  index makes concurrent creation safe. total = not cancelled; "confirmed" = confirmed + rescheduled + arrived;
  "not yet confirmed" = pending. Open slots = start of each future gap >= 20 min (max 6) on one line.
- **post_visit** needs `clinic.review_link`; without it the job is skipped (`no_review_link`) rather than sending a
  sentence that ends in nothing. **recall** is skipped if the patient already has an upcoming appointment.
- **Simulator statuses.** `sent` is recorded inline; `delivered` (~1 s) and `read` (~3 s, or immediately when the chat
  is open) are emitted as Meta-shaped status payloads through the same handler real status webhooks use. Statuses never
  move backwards; `failed` keeps Meta's error code/title in the message log.
- **Seeded appointments have no reminder jobs.** The seed inserts rows directly; only appointments created through the
  service/API are scheduled. Use `POST /demo/fire` (Phase 6 control strip) to send reminders for seeded ones.
- **Free-form (session) messages** (text, interactive list/buttons) are skipped with `window_closed` unless the patient
  messaged us in the last 24 h.

## Phase 3
- **Where it lives.** `messaging/inbound.py` (dispatch), `messaging/optout.py` (STOP detection),
  `services/reschedule.py` (slot finder, list builder, row ids). Everything runs inside the webhook handler's
  per-event transaction, so a failed action also rolls back its dedupe row and Meta's retry can safely redo it.
- **Idempotency.** State is decided under a row lock (`select ... for update`), not by remembering message ids, so
  genuinely repeated or concurrent taps (new message ids) are no-ops: second Confirm = silent no-op; second Cancel =
  the "no longer active" reply, no state change. Five concurrent Confirms produce one status event and one reply.
- **Ownership.** A button/row carrying someone else's appointment id is treated as "no longer active"; nothing about
  that appointment is revealed or changed. Malformed ids are ignored safely.
- **Button text fallback.** A template button with no payload maps its visible text (Confirm/Reschedule/Cancel,
  English only for now) to the action, applied to the patient's soonest upcoming active appointment.
- **Reschedule list.** Own doctor first (looking 7 days ahead, >= 2 h out, <= 3 per day, spread earliest/middle/latest
  so a day offers morning and evening); other doctors only fill in if the patient's doctor has fewer than 9 slots in
  the whole window. The appointment's own current slot is never offered, but its neighbours are (its own booking
  does not block a small shift). Row title e.g. "Tue 10 Nov · 10:30 AM", description = doctor name, last row
  "None of these work" in an "Other" section.
- **Picking.** One transaction via `move_appointment` (exclusion constraint is the judge). Success -> status
  `rescheduled`, reminders regenerated, and the *new confirmation job is executed immediately* (same code path as the
  scheduler, so no duplicate and the wording is the booking-confirmation template). Slot taken / invalid -> apology
  as its own message, then a fresh list. A pick that is now < 2 h away, or for a dead appointment, gets a fresh list /
  the inactive message respectively. Picking the current time is acknowledged, not re-booked.
- **"None of these work" and Recall "Book now"** both create an inbox item and send the (rate-limited) acknowledgement.
  Recall "Not now" gets a short polite reply. These two were not specified; they exist so no button is a dead end.
- **STOP detection is deliberately narrow:** only messages of <= 3 words, containing stop / unsubscribe (or a
  one-edit typo such as "stopp") or the Hindi/Marathi words बंद, रोको, थांबा..., and **never** if a negation is present
  ("don't stop", "मत बंद करो"). So "can you stop by at 5 pm?" goes to the inbox, not the opt-out list. The word lists
  need native-speaker review. There is no START / re-subscribe keyword: not in the brief, so reception must handle it.
- **Opt-out confirmation** ("You will not receive further WhatsApp updates from {clinic}") is the one message allowed
  past the opt-out guard (`bypass_opt_out`), as the reply to the patient's own STOP. Repeat STOPs get nothing. A
  later free-text from an opted-out patient still reaches the inbox but gets no auto-reply. Scheduled jobs are not
  deleted on opt-out; they are skipped at execution with `skip_reason = opted_out` (visible in the log).
- **Replies are session messages** (proactive = false): never delayed by quiet hours; they rely on the 24 h window that
  the tap itself opens. Language is English until the wow phase.
- **Auto-acknowledgement** ("Thanks, our reception team will reply shortly.") at most once per 12 h per patient,
  detected from the patient's own outbound messages; no extra column.
- **Waitlist hook.** Cancel and a successful reschedule call `waitlist.offer_freed_slot(...)` with the freed slot
  (doctor, start, duration). It is a no-op stub until Phase 4. The 60-minute minimum notice is already encoded there.
- **Ordering fix (migration 004).** `now()` is frozen per transaction, so a tap and the replies it triggers shared a
  timestamp and could sort arbitrarily in the chat. `outbound_message`, `message_event`, `appointment_event` and
  `inbox_item` now default `created_at` to `clock_timestamp()`.
- **Inbox API not built.** Inbox items are created and tested, but listing, marking handled and replying from the
  dashboard arrive with the reception UI (Phase 5).
- **Bugs the tests caught on the way:** `spread()` divided by zero when exactly one slot was wanted (reachable when the
  list is one row short of nine); fixed and covered.

## Phase 4
- **Engine** (`services/waitlist.py`). One entry point, `offer_freed_slot`, called by Cancel, reschedule-away, Not now,
  expiry and staff removal. It only acts if the slot starts > 60 min from now, the clinic is not in quiet hours, and
  nobody already holds a pending offer for that slot (an advisory lock per slot serialises concurrent triggers; the
  unique index is the backstop). Five concurrent triggers -> exactly one offer and one message.
- **Eligibility, in order:** entry `waiting` for that IST date; doctor matches (or "any"); window covers the slot
  (morning < 14:00, afternoon 14:00-17:00, evening >= 17:00, or "any"); patient not opted out; this entry never
  already offered *this* slot; the candidate's treatment duration fits inside working hours and is clear of every
  active appointment for that doctor; the patient has no overlapping appointment. First come first served by
  `created_at`. The **offer's duration is the candidate's treatment duration**, not the freed appointment's: a 60-minute
  visit is skipped when only 20 minutes are free.
- **Offers are proactive sends**, so they obey quiet hours: a cancellation at night makes **no** offer (the slot shows
  as an open gap) rather than deferring one. A candidate whose send is refused (opted out meanwhile, failed transport)
  is rolled back cleanly and the next candidate is tried.
- **Not now / expiry:** the entry goes back to `waiting` (so they still get other openings) but is never offered the
  same slot twice; the slot moves to the next candidate. The tick expires overdue offers one per transaction with
  `SKIP LOCKED`, so concurrent ticks create exactly one follow-on offer. A Book tap after the expiry time but before
  the tick gets the same treatment: politely declined, offer expired, slot passed on. Expiry never offers a slot that
  is by then < 60 min away.
- **Book** (one transaction, offer row locked): creates the appointment with `source=waitlist`,
  `recovered_via_waitlist=true`, and **status `confirmed`** (the patient actively chose it, and a second "please
  confirm" reminder would be silly, so no day_before is scheduled); entry -> `booked`, offer -> `accepted`; the
  booking-confirmation template is sent immediately through the normal job path. Slot taken in the meantime (the
  exclusion constraint decides) -> `slot_taken` + apology, entry stays on the list. If the patient now has an
  overlapping appointment elsewhere, the offer is released with a short explanation. Repeat Book/Not-now taps are silent
  no-ops; someone else's offer id is answered "no longer available" and changes nothing.
- **Waitlist API** was pulled forward from the reception phase because the engine is not operable without it:
  list (upcoming, waiting/offered; a pending offer carries `offer_expires_at` for the countdown), add (existing or new
  patient), remove (an entry holding the offer hands it on). The manager *screen* is Phase 5.
- **Running-late broadcast** (`services/broadcast.py`): that doctor's remaining appointments today (IST) with status
  pending/confirmed/rescheduled, starting after now, patient not opted out; **one message per patient** with their
  *earliest* remaining time. Delays are exactly 15/30/45/60. Preview returns the exact rendered text (a sample when
  nobody qualifies) and the recipient list. Sending uses one transaction per recipient and counts sent / skipped (by
  reason, e.g. `quiet_hours`) / failed rather than aborting. A repeat of the same doctor+delay within 60 s is refused
  (409) as a double-click. No recipients -> nothing sent and no `broadcast` row. Broadcasts are proactive, so quiet
  hours apply.
- **Reply to a cancelled slot** is not automatic for the patient who cancelled; only waitlisted patients are contacted.
- **Seeded flow is a test.** Seed -> cancel the "cancel me" appointment -> offer reaches waitlist entry #1's patient ->
  Book -> slot filled -> recovered count 18 -> 19 (the acceptance test's headline path, minus the analytics page).
- **UI fix (found while demoing):** the Simulator no longer jumps to the newest chat; it pins the chat the presenter
  chose, and other chats with new activity get a dot.
- **Test hygiene note:** offers are proactive, so waitlist tests neutralise the clinic's quiet hours (00:00-00:00);
  otherwise they would fail whenever run between 21:00 and 08:00 IST. Quiet-hours behaviour has its own test with an
  explicit clock.

## Phase 5
- **Gap closed from Phase 4.** Only the *patient's* Cancel/reschedule used to feed the waitlist; reception cancelling or
  moving a card through the API did not. `POST /appointments/{id}/status` (to cancelled) and `PATCH` (time/doctor change)
  now call the same engine. This is what makes the demo's "cancel a seeded appointment" step produce an offer.
- **Routes and gating.** Staff pages live in an `(app)` route group behind `AuthGate`; the role is read from `/me`
  (staff_profile), never from the token. `AuthGate` takes `roles`, and the nav items have `roles`, so Analytics/Settings
  (owner only) slot in later without new plumbing; today reception simply has no such links.
- **Login.** Local mode = the dev sign-in buttons. With the two public Supabase env vars set it is email/password through
  supabase-js and the stored token follows Supabase's refreshes. The ONLY Supabase value in the bundle is the anon key.
  **Not verified against a real Supabase project** (none available here).
- **Realtime.** With Supabase configured the app subscribes to `postgres_changes` on appointment, outbound_message,
  message_event, waitlist_offer and inbox_item and refreshes all queries (debounced 250 ms); if the channel is not
  SUBSCRIBED it falls back to 3 s polling automatically. Without Supabase it always polls. **The Realtime path is
  unverified**; the polling path is what the tests exercise (the 5-second acceptance bound holds with polling alone).
- **Toasts come from data, not guesses.** List rows carry the latest status event (`last_event_actor/status/at`). The UI
  diffs successive polls: a card whose latest event changed pulses; a *patient/system* change raises "X confirmed via
  WhatsApp · just now" (reception's own actions do not toast, they just happen). A new waitlist-sourced appointment raises
  "booked a freed slot via WhatsApp". The first load of a day announces nothing.
- **Day grid.** One column per doctor, 15-minute rows (30 px). Visible time = every doctor's working hours *plus* any
  appointment outside them (so nothing is ever hidden), with a "Closed" band between sessions and a hatch where a doctor
  does not work. Overlapping cards (e.g. a cancelled card over a re-booked slot) sit side by side in lanes. Gaps >= 20
  minutes (the shortest treatment) become dashed "Open slot" blocks, clickable to book; past times are not offered.
  Cards show name, time, status chip, source icon and only the **last 4 digits** of the phone; ⚠n marks repeat no-shows.
- **Counts bar:** Total = everything not cancelled; Confirmed/Pending/Cancelled/Arrived are exact status counts, and
  Rescheduled / No-show appear only when non-zero (so the bar always adds up).
- **Call list** = status `pending` only, starting in the next 12 h. "Called — confirmed" is a normal confirmation with
  `detail.via = phone`, shown in the message log as "(called)". On a Sunday / after hours it is legitimately empty.
- **Drawer rules:** Mark arrived any time while upcoming; **No-show only once the start time has passed**; Cancel asks for
  confirmation and says the slot goes to the waitlist; terminal statuses lock all actions.
- **Add/edit form:** only slots from the API are offered (the exclusion constraint still decides the race). Losing a race
  shows "That time was just taken by another booking", drops the stale slot and refetches. Patient: search or create with
  +91 validation (10 digits, 6-9), language, and a **required** consent source. An existing phone number is matched, never
  duplicated. Edit mode offers the appointment's own time as "current".
- **Message log** = one chronological timeline per appointment: messages (with sent/delivered/read tick times, Live vs
  Simulator badge, failure text), the patient's taps and list choices, and status changes. Waitlist offers are not tied to
  an appointment, so they appear in the Simulator chat, not in a card's log.
- **Inbox:** reply is a free-form session message and is **disabled with the reason shown on screen (and as a tooltip)**
  when the 24 h window is closed or the patient opted out; the server enforces the same and also runs the medical-content
  guard on staff text. Sending a reply marks the item handled.
- **Accessibility fix found by the tests:** the add-appointment Date field wrapped three controls in one `<label>`, giving
  it an accessible name like "Date ‹ ›". It is now a label bound to the input only.
- **No shadcn/ui.** The brief lists it, but these screens needed few primitives (modal, chip, field); they are small and
  local (`components/ui.tsx`) and use CSS-variable tokens that follow light/dark. Revisit if the owner pages need more.
- **Playwright** runs on the installed Edge (no browser download). 14 tests: login/role gating, the day's data and counts,
  the 5-second tap-to-card acceptance test (confirm *and* cancel), reception-cancel -> waitlist countdown, call list,
  message log, running-late, add/edit with validation and a real lost-race, waitlist add/remove, patients, and inbox
  (including the closed-window state, produced by ageing the patient's messages in the database).

## Phase 6
- **Analytics definitions** (shown in the UI, implemented in `services/analytics.py`): no-show rate = no-shows /
  (arrived + no-shows); slots recovered = non-cancelled appointments booked from the waitlist; revenue saved = recovered x the
  clinic's average ticket (the formula is printed on the tile); WhatsApp confirmation rate = of appointments that reached a
  settled state, the share the *patient* confirmed on WhatsApp (a phone confirmation by reception does not count).
  Empty periods return `null`, never 0%, and empty weeks are gaps in the line, not zeros.
- **Seed now includes confirmation history** for the post-go-live period (patient confirmations on ~80% of kept visits, ~20%
  of no-shows; waitlist recoveries are patient-booked) so the confirmation rate has data. It uses a separate random stream, so
  every earlier seeded number is unchanged.
- **ROI is conservative first.** The headline uses only what is *measured* (slots recovered x ticket) when that alone covers the
  plan; the "no-shows avoided" figure (baseline - current rate, applied to completed visits) is an *estimate*, always labelled as
  one and shown as upside. Window: the last 30 days plus bookings already made for the next 7. The plan price (Rs 2,999) is a
  constant in the API response, not a clinic setting.
- **"Simulated data"** appears when any seeded patient is among the appointments behind the numbers (the whole 13-week window):
  "Simulated data" at 100%, "Includes simulated data (n%)" otherwise.
- **Charts** follow the data-viz skill: one validated hue (slot-1 blue, checked in light and dark), 2 px line with 8 px markers
  ringed in the surface colour, 4 px-rounded bar tops on the baseline, recessive grid, a labelled baseline and go-live marker,
  hover tooltips, selective labels, a "View as table" alternative, and a text summary for screen readers. Straight segments, not
  smoothing, so nothing is implied between weeks.
- **Presenter view.** The prospect is booked at the earliest slot that **no waitlisted patient is waiting for**. Found by the
  end-to-end test: otherwise rescheduling the prospect frees a slot the waitlist engine (correctly) offers at once, using up
  the offer meant for step 4. Reset keeps staff logins and the messaging mode. The doctor summary shows the first working day
  from today (so a Sunday demo shows Monday), sent regardless of quiet hours. The schedule opens on the next working day
  when today is closed.
- **Live mode is refused** (409, with the reason) until the Live transport exists *and* WA credentials are set; Simulator is
  always one click away. `LiveTransport.implemented` is the switch Phase 7 flips.
- **Public booking.** Unauthenticated, so: Origin allow-list (a browser sending a disallowed Origin gets 403; non-browser clients
  without an Origin are allowed and rate-limited), 5/min and 30/hour per address (in memory, one API process), a honeypot that
  looks successful but creates nothing, mandatory consent (recorded as `website_form` with a timestamp), 2-hour minimum lead and a
  60-day horizon. Responses are identical whether or not the number is already a patient or opted out (no enumeration), and a
  web form never re-subscribes an opted-out patient. "Any doctor" picks the first free doctor alphabetically. `X-Forwarded-For`
  is ignored unless `TRUSTED_PROXY_HOPS` > 0 (it is spoofable), proven by a test. **The brief's [FILL IN] dental-website URL is
  still blank:** the `<iframe>`/API snippets work for any site; add its origin to `PUBLIC_BOOKING_ALLOWED_ORIGINS` for direct posts.
- **Framing.** `/book/embed` may be framed (any site by default, or `NEXT_PUBLIC_EMBED_ORIGINS`); every other page sends
  `frame-ancestors 'self'`, so the staff dashboard cannot be clickjacked.
- **Import.** Required: patient name, phone, doctor, date, time; optional: treatment (default Consultation), language, note.
  Dates (ISO, DD/MM/YYYY, "10 Nov 2026"...) and times (24 h, "10:30 AM") are parsed tolerantly; a doctor name matches loosely but
  never between two doctors ("Neha" with two Nehas is an error). Past rows, working-hours violations, clashes with existing
  bookings and clashes *within the file* are per-row errors. **Commit re-validates from the raw CSV**; it trusts nothing from
  the preview. Valid rows are created each in its own savepoint; re-importing is a no-op ("Already booked"). Imported patients are
  recorded as consent source `import` and the person importing must tick that they agreed to WhatsApp updates. **No booking
  confirmation is sent by default** (reminders are scheduled either way): a confirmation to every imported patient at once is
  rarely wanted, and there is a checkbox for when it is. Limits: 1,000 rows, 2 MB.
- **Google Sheet fetch is SSRF-safe:** https only, host must be `docs.google.com` (redirects only to `*.googleusercontent.com`,
  max 3, each re-checked), no credentials or odd ports in the URL, 10 s timeout, 2 MB cap, CSV content-type required. A normal
  share link is rewritten to its CSV export link (works only if the sheet is link-shared). Verified with a mock transport: a
  malicious redirect to the cloud metadata address is never requested.
- **Not built in this phase (and not yet scheduled anywhere in the brief's phases):** the **Settings** screen (clinic details,
  hours, doctors, treatment types, reminder toggles, quiet hours, TTL, template preview, messaging mode + health check).
  Only the health check was named in "Go live". Needs a decision on when to build it (proposed: Phase 7).
- **Test hygiene:** the test harness now forces the app's own `DATABASE_URL` to the test database, because `/demo/reset` re-seeds
  (wipes) whatever the app points at, and a test must never be able to touch development data.

## Phase 7
- **Live transport** (`messaging/transport.py`). Templates are sent as Cloud API *template* messages with the ordered variable values
  (`Rendered.params`) and one `quick_reply` button component per button carrying our payload (`CONFIRM:{id}`...); lists/buttons/text use the
  session message shapes. Meta error codes are always kept (`meta_131047: ... (the 24-hour window is closed)`), with a plain-language hint
  for the ones owners will actually meet (token, allowed-recipient list, window, template missing/paused). **Written to Meta's documented
  shapes and tested only against a mock; never run against Meta.**
- **Retry rules.** HTTP 429/5xx and Meta throttle codes are retried inside the transport (3 tries, honouring Retry-After), then the
  scheduler retries the *job* later (3 attempts). Connection failures are retried (nothing was sent). **A read timeout is NOT retried** and is
  not "transient": the request may have been delivered, Meta has no idempotency key, and a retry could message a patient twice. It shows as
  failed with a clear reason. Seeded recipients are refused inside the transport as well as routed to the Simulator.
- **Migrations are run by `python -m app.migrate`**, not pasted by hand: one transaction per file, recorded in `schema_migrations`,
  advisory-locked so two containers starting together apply each file once, `--baseline` for a database built by hand. The container runs it on
  every start. Facts for the health check live in a tiny `system_status` table (migration 005); "last webhook received" is written only after
  the signature verifies.
- **Settings (owner only)** covers clinic details, quiet hours, reminder toggles/offsets, doctors and working hours, treatments, template
  preview, WhatsApp health and the Live switch. Rules: (1) anything that appears in patient messages (clinic name, address, parking note, a
  doctor's name) is checked against the medical-content guard **at save time**, otherwise one bad edit would make every send fail; a new
  treatment name that also appears in that text is refused for the same reason; (2) names must be unique (case-insensitive) so imports and messages
  stay unambiguous; (3) at least one doctor stays active; (4) hours are IST on the 15-minute grid, no overlaps; (5) **nothing here ever changes an
  existing appointment**: changing hours reports how many upcoming bookings now fall outside them; (6) treatments cannot be deleted (history
  points at them), only renamed or edited; durations are multiples of 5 minutes; (7) the clinic's own phone may be a landline.
- **Going Live is gated.** `PUT /settings/messaging-mode` refuses Live (409 with the blockers) unless credentials are set, the app secret and verify
  token exist, and Meta accepts the token *right now*. Template approval and "no webhook yet" are warnings, not blockers (rehearsals on a test number
  are legitimate). Simulator needs no checks: it is the fallback. The health check never returns a secret (only booleans and Meta's public facts) and
  caches Meta's answer for 30 s.
- **Submitting templates** to Meta from Settings requires an explicit confirmation, reports each template (submitted / already exists / error with
  Meta's reason / skipped when a language is not written) and treats Meta's "already exists" subcode as success.
- **Deployment artifacts:** `api/Dockerfile` (built from the repo root; non-root user; migrate-then-serve; health check), `.dockerignore`,
  `railway.json` (one replica, `/health`). Verified by building the image and running it against an empty database. Not deployed: that needs the
  owner's Supabase, Meta, Railway and Vercel accounts.
- **Production safety:** `DEMO_MODE=false` removes the Simulator and demo endpoints (verified in the container); `/auth/dev-login` additionally
  refuses to exist beside a configured Supabase project; the staff app sends `frame-ancestors 'self'`.
- **Bugs the final verification found and fixed:**
  (1) the seed laid 20-minute visits end to end, so appointments started at 10:20, 10:50...: **off the 15-minute grid the product books on**, which
  made the waitlist engine refuse to offer those slots (so the live demo's step 4 depended on a random choice) and made "current slot" disappear when
  rescheduling. The seed now snaps to the grid and asserts it for every appointment;
  (2) the Settings forms were re-keyed by server data and **discarded in-progress edits** when the page refetched: they now keep their state and adopt
  the server's normalised values from the save response;
  (3) saving the clinic card re-sent the stored landline phone, which a mobile-only validator rejected;
  (4) the import wizard could lose a column choice made quickly after another, and a slow older preview could overwrite a newer one;
  (5) a test fixture leaked `system_status` between tests.
- **Not built (wow phase):** Hindi/Marathi templates by patient language, Google Calendar sync, Razorpay deposit links. The Hindi/Marathi guard
  lists and STOP words still need native-speaker review.

## Phase 8
- **Scope decision: the Google Calendar item was built as a private calendar *feed*, not a two-way integration.** Real sync needs Google OAuth
  (consent screen, token storage, refresh, revocation) that cannot be tested here and that a clinic should not trust to a demo. A per-doctor
  `.ics` URL (migration 007) gives the visible win (appointments show up in the doctor's own calendar) with no Google credentials at all.
  Trade-offs, all stated in the UI: Google refreshes subscribed calendars only every few hours; read-only; the URL is the credential (64 hex
  characters, rotatable, rate-limited, never in a log or an API response other than the owner's Settings). Privacy: the title is the patient's
  name only, no phone, treatment or notes; cancelled/no-show events are left out so they vanish at the next refresh. If a real integration is
  ever wanted, this feed is the safe fallback to keep.
- **Hindi and Marathi texts are first drafts.** Structure is enforced by tests (same variables in the same order, button labels within Meta's
  25 characters, no variable at the start or end of a body, `STOP` kept in Latin letters because that is what patients type, every rendered
  text passes the medical-content guard) but wording and tone were not reviewed by a native speaker, and the guard word-lists are likewise
  unreviewed. Settings says so on screen. Meta must approve each language separately; the health card shows per-template status.
  Not translated: the doctor's morning summary, free-text session replies (slot lists, confirmations after a tap) and the dashboard itself.
- **Deposit design.** Money never flows through this system: it creates a payment link (`reference_id` = appointment id, generic description
  "Appointment booking deposit", amount 100 to 50,000 rupees, 48 h expiry, Razorpay's own SMS/email notifications off) and reads signed webhooks.
  Webhook rules: HMAC-SHA256 over the raw body with its own secret (refused outright if no secret is set); idempotent (Razorpay retries); a
  payment whose amount differs from what we asked is **not** marked paid and is logged for a human; unknown links are ignored. A fresh request is
  allowed after expiry; none after payment. Only for upcoming appointments still on; never for patients who opted out. The patient message
  goes through the normal pipeline (guards, quiet hours, language, opt-out), and a deposit request for a seeded patient never calls Razorpay.
  Deliberately not built: refunds, forfeiting on no-show, receipts, GST, reconciliation. The "demo mode with no keys" fake link and the
  *Simulate: patient paid* button only exist when `DEMO_MODE=true` and the link id starts with `plink_demo_`.
- **Side effect worth knowing:** because the seed gives some patients Hindi or Marathi, English-text assertions in the browser tests had to pick
  English-speaking patients (`findUpcoming(..., language)`). The product behaviour is right; two older tests assumed every patient read English.
- **Not verified against real services:** Razorpay's real API and webhooks (tested against its documented shapes through a mock), Meta's approval of
  the new templates, Google Calendar's handling of the feed (tested as valid iCalendar by our own tests, not imported into Google).
