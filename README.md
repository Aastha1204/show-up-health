# ShowUp · by Neovia AI — WhatsApp appointment reminders (Demo 03)

Dental-clinic no-show reduction demo: one-tap Confirm / Reschedule / Cancel on WhatsApp, waitlist refill, live
reception dashboard, ROI analytics. Live WhatsApp and an in-browser Simulator share one code path.

**Status: all 8 build phases are built and verified locally (the "wow" phase is done as scoped in DECISIONS.md). NOT YET
DEPLOYED, and the Live WhatsApp transport, Razorpay and Supabase login/Realtime have never been run against the real services.**
See "What is verified, and what is not" below.

## Layout
```
/api        FastAPI app (app/, tests/)         /db/migrations  plain SQL, run in order
/web        Next.js app (Phase 5)              /templates      WhatsApp template JSON (Phase 2)
/e2e        Playwright (Phase 5+)              DECISIONS.md    choices made where the brief was silent
```

## Local setup (Phase 1)
Requirements: Python 3.11+, Docker (Postgres 16 with `btree_gist`).

```bash
docker compose up -d db
cp .env.example .env          # defaults already point at the compose database
python -m venv .venv && .venv/Scripts/activate        # Windows; use .venv/bin/activate elsewhere
pip install -r api/requirements.txt
```

Create the schema and the demo data (run from `/api`):

```bash
cd api
python -m app.migrate        # applies db/migrations in order, once each; safe to repeat
python -m app.seed                      # wipes and reseeds relative to the current IST time
uvicorn app.main:app --reload           # http://127.0.0.1:8000/docs
```

On Supabase, point `DATABASE_URL` at the project's Postgres connection string and run the same `python -m app.migrate` (a database that was set up earlier by pasting the SQL needs `python -m app.migrate --baseline` once). Then create the two demo users:
`python -m app.seed --create-auth-users` (needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`; **not yet exercised**
against a real project).

| Demo user | Role | Password |
|---|---|---|
| owner@smilecare.demo | owner | `ShowUp-Owner-2026` |
| reception@smilecare.demo | reception | `ShowUp-Reception-2026` |

Fictional clinic and users; change the passwords via `DEMO_OWNER_PASSWORD` / `DEMO_RECEPTION_PASSWORD` before seeding.

### Tests
```bash
cd api
pytest                                   # pure tests only (timezone, slots, phone, templates, guards): no database
```
Full suite including the DB tests (create a throwaway database first):
```bash
docker compose exec db psql -U postgres -c "create database showup_test"
cd api
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/showup_test pytest
```
`TEST_DATABASE_URL` is **dropped and rebuilt** each session — never point it at real data.

## Run it locally (Phase 2)
No Supabase needed for local development: leave `SUPABASE_URL` empty, set `SUPABASE_JWT_SECRET` to any long random
string and `DEMO_MODE=true` in `.env`; that enables a local-only `/auth/dev-login` (it refuses to exist next to a
configured Supabase project).

```bash
cd api && python run.py                 # API on :8000 (run.py = Windows-safe launcher; elsewhere `uvicorn app.main:app` works)
cd web && npm install && npm run dev    # web on :3100  (3000 is often taken; /api/* is proxied to :8000)
```
Open http://localhost:3100/simulator, sign in, and book an appointment (`POST /appointments`, or Swagger at
`:8000/docs`) for a seeded doctor. The confirmation arrives within ~30 s (the in-process scheduler ticks every 30 s;
`POST /internal/tick` with `X-Internal-Secret` is the cron backup). `POST /demo/fire` sends any reminder immediately.

**Messaging pieces:** templates live in `/templates/*.json` (one file per template, per-language blocks, Meta-ready
via `to_meta_template()`); `app/messaging/` holds renderer, guards, transports, the send pipeline and the webhook
handler. Everything the Simulator phone does goes through `POST /simulator/inbound` (Meta webhook payload shape) into
the *same* `handle_payload()` as `POST /webhooks/whatsapp`; only the signature check differs.

**Templates:** submit the eight `showup_*` templates in WhatsApp Manager (or via the templates API using
`to_meta_template`) as early as possible; approval takes time. Meta may re-categorise `showup_post_visit` and
`showup_recall` as *marketing* rather than *utility*, which changes their price. Hindi/Marathi arrive in the wow phase.

**Webhook (Phase 7 completes this):** callback URL `https://<api-host>/webhooks/whatsapp`, verify token = `WA_VERIFY_TOKEN`,
signature secret = `WA_APP_SECRET`. For local testing expose :8000 with cloudflared/ngrok.

## The reception UI (Phase 5)
Open http://localhost:3100 and sign in (local mode shows two buttons; with `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` set it shows the real email/password form, **not yet tested against a real project**).
Updates arrive by Supabase Realtime when configured, otherwise every live screen polls every 3 s (the header shows
"Live" or "Polling"). Today shows IST everywhere; on a closed day (Sunday) it offers a jump to the next working day.

### End-to-end tests (Playwright)
Needs the stack running (`docker compose up -d db`, API on :8000, web on :3100; running servers are reused).
```bash
cd e2e && npm install
set E2E_PYTHON=C:\path	oenv\Scripts\python.exe     # the Python that can run `python -m app.seed` in /api
npx playwright test                                        # re-seeds first; uses your installed Edge (E2E_CHANNEL=chrome for Chrome)
```
`E2E_SKIP_SEED=1` keeps the current database. The call-list test skips itself when nothing starts in the next 12 hours
(e.g. on a Sunday) because that panel is then legitimately empty.

## Owner & demo (Phase 6)
- **Analytics** (`/analytics`, owner only): no-show rate vs baseline, slots recovered, revenue saved (formula shown),
  WhatsApp confirmation rate, an ROI card, and two charts (weekly no-show rate with the baseline and a "Reminders went
  live" marker; slots recovered per week), each with a table view. A **Simulated data** badge appears whenever the numbers
  come from seeded patients.
- **Presenter view** (`/demo`): phone on the left, live schedule on the right, and a control strip that follows the
  3-minute script: add the prospect (books the next working day at the next free slot and confirms now), fire any reminder,
  cancel the seeded appointment (the offer goes to the waitlisted patient), waitlist offer lifetime (2 / 15 min), the
  doctor's 8 a.m. summary, Live/Simulator toggle, and **Reset demo**. See `DEMO_SCRIPT.md` (Phase 7) for the click path.
- **Public booking** (`/book`, `/book/embed`): no login. Mandatory consent checkbox, honeypot, per-address rate limit,
  Origin allow-list (`PUBLIC_BOOKING_ALLOWED_ORIGINS`). The `/book` page shows an `<iframe>` snippet for the clinic's
  website and a direct-API snippet. Set `TRUSTED_PROXY_HOPS` when the API sits behind proxies (otherwise every visitor looks
  like one address) and `NEXT_PUBLIC_EMBED_ORIGINS` to restrict who may frame the form (default: anyone; the staff app can
  never be framed).
- **Import** (`/import`): CSV upload or a published / link-shared Google Sheet; column mapping; every row validated
  (phone, doctor, date/time, working hours, clashes) before anything is saved; explicit consent confirmation; re-importing
  is safe. Only `docs.google.com` links are fetched (SSRF-safe).

## Going live

### What you need
| Piece | Used for | Notes |
|---|---|---|
| **Supabase** project | Postgres, staff login, Realtime | Run `python -m app.migrate` against its connection string. Create the two staff users with `python -m app.seed --create-auth-users` (needs the service-role key). |
| **Meta WhatsApp Business** account | real WhatsApp messages | Test number: only 5 pre-verified recipients, fine for rehearsal. For a prospect's phone register a **dedicated real number** (a spare SIM, never a personal number). |
| **Railway** (or any always-on host) | the API | One replica only: the 30-second reminder scheduler runs inside it. Free tiers that sleep will stop reminders. |
| **Vercel** | the web app | Root directory `web`. |

### Environment variables
API (Railway): `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET` (only for older HS256 projects; otherwise JWKS is used), `SUPABASE_SERVICE_ROLE_KEY`,
`WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_BUSINESS_ACCOUNT_ID`, `WA_APP_SECRET`, `WA_VERIFY_TOKEN`, `WA_API_VERSION`, `INTERNAL_TICK_SECRET`,
`PUBLIC_BOOKING_ALLOWED_ORIGINS` (the web app's URL and the clinic website's), `PUBLIC_API_URL`, `TRUSTED_PROXY_HOPS` (1 on Railway), `APP_TIMEZONE=Asia/Kolkata`.
**`DEMO_MODE` must be `false` in production**: it enables the Simulator endpoints, the demo controls (including *Reset demo*, which wipes data) and nothing else.
Web (Vercel): `API_URL` (the API's public address; the browser only ever calls `/api/*`, which Next proxies), `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
optionally `NEXT_PUBLIC_EMBED_ORIGINS`. The anon key is the only Supabase value that reaches the browser.

### Steps
1. **Database:** create the Supabase project; `DATABASE_URL=… python -m app.migrate`; optionally seed (`python -m app.seed`) for a demo clinic.
2. **API:** deploy the repository root to Railway (`railway.json` builds `api/Dockerfile`; migrations run on every start; health check `/health`, readiness `/health/ready`). Set the variables above.
3. **Web:** import the repo into Vercel with root directory `web`, set the variables, deploy.
4. **Meta:** in the Meta app, set the webhook callback to `https://<api-host>/webhooks/whatsapp` with your `WA_VERIFY_TOKEN`, and subscribe to **messages**. (Meta calls the API directly, not through Vercel.)
5. **Templates:** *Settings > WhatsApp > Submit templates to Meta* (or paste the JSON from `/templates` into WhatsApp Manager). Approval takes time: **submit early**. The buttons sent must match the approved templates. Meta may re-categorise `showup_post_visit` and `showup_recall` as *marketing*, which changes their price.
6. **Check:** *Settings > WhatsApp* shows what is missing (token valid, app secret, verify token, template approval, last webhook received). **Live can only be switched on when it has no blockers.** Simulator is always one click away.
7. **Rehearse** with the Meta test number and one of your 5 verified phones before using a prospect's phone.

### What is verified, and what is not
- **Verified here:** every behaviour through the Simulator (the same send pipeline, webhook handler, state machine and database as Live); the Live transport's *requests and error handling* against Meta's documented request/response shapes (mock HTTP); the Docker image builds, runs as a non-root user, applies migrations on start and reports ready; the whole stack from an empty database on a **production** web build passes the full Playwright suite.
- **NOT verified (needs real accounts):** that Meta accepts these exact template payloads and approves the templates; sending to a real phone; Meta's real webhooks and signatures; Supabase email/password login, JWKS token verification and Realtime. The Supabase and WhatsApp code paths are written to the documented APIs and tested only against fakes, Razorpay's real API and webhooks, and Meta's approval of the Hindi/Marathi/deposit templates.
- **Not built:** a two-way Google Calendar integration (a read-only calendar feed exists instead), refunds/receipts for deposits, and translations of free-text replies and the dashboard. Hindi/Marathi template text is a first draft that needs a native speaker's review (see Phase 8).
- **Known limits:** the public-booking rate limiter is in memory (one API process); an ambiguous Live send (timeout after the request left) is not retried, to avoid messaging a patient twice, and shows as failed with a clear reason.

## Phase 8: extras

**Hindi and Marathi messages.** All seven patient-facing templates now have Hindi and Marathi text (`/templates/*.json`). A patient's
*Language* field picks the text; anything else falls back to English. The doctor's morning summary stays English. Dates and times keep
their English format ("Tue 10 Nov"). **These are first drafts written without a native speaker: have one read them, and let Meta approve
each language, before using them with real patients.** Settings > Message templates previews every language.

**Booking deposit (Razorpay payment link).** On an upcoming appointment, *Request deposit (₹500)* creates a Razorpay payment link and sends
it to the patient on WhatsApp (template `showup_deposit_request`, UTILITY). Razorpay tells us when it is paid (`POST /webhooks/razorpay`,
signature-checked) and the appointment shows *Deposit paid*. Setup: set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (use `rzp_test_` keys first)
and `RAZORPAY_WEBHOOK_SECRET`; in the Razorpay dashboard add a webhook to `https://<api-host>/webhooks/razorpay` for the events
`payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`. Without keys, and only in `DEMO_MODE`, the button creates a clearly
fake link and the drawer offers *Simulate: patient paid*, so the flow can be demoed. Seeded patients always get the fake link. Not built, on
purpose: refunds, keeping a deposit after a no-show, receipts and reconciliation (clinic policy and accountant decisions).

**Calendar feed (instead of a Google Calendar integration).** *Settings > doctor > Calendar link* is a private `.ics` URL. In Google Calendar:
*Other calendars > + > From URL*. It shows patient names and times only (no phone numbers, treatments or notes); cancelled and no-show
appointments drop out. Google refreshes subscribed calendars every few hours, so it is a view, not instant sync, and it does not write to
Google. Anyone holding the link can read that doctor's schedule: use *New link* if it leaks (the old one dies at once). The link must be
reachable from the internet, so it only works once the app is deployed.

## API so far
`GET /health` · `GET /me` · `GET /reference` · `GET /slots` · `GET|POST /appointments` · `GET|PATCH /appointments/{id}` ·
`POST /appointments/{id}/status` · `GET|POST /patients` · `GET|POST /webhooks/whatsapp` · `POST /simulator/inbound` ·
`GET /simulator/threads[/{kind}/{id}/messages]` · `POST /simulator/threads/{kind}/{id}/read` · `POST /demo/fire` ·
`GET /appointments/call-list` · `GET /appointments/{id}/log` · `GET /inbox[/count]` · `POST /inbox/{id}/handled|reply` ·
`GET /analytics` (owner) · `GET|PATCH /settings…`, `GET /settings/health`, `PUT /settings/messaging-mode`, `POST /settings/templates/submit` (owner) · `GET /health/ready` · `POST /import/preview|commit` · `GET /public/clinic|slots` · `POST /public/appointments` ·
`GET /demo/state` · `POST /demo/prospect|cancel-seeded|summary|reset` · `PUT /demo/ttl|mode` ·
`GET|POST /waitlist` · `DELETE /waitlist/{id}` · `GET /broadcasts/running-late/preview` · `POST /broadcasts/running-late` ·
`POST /auth/dev-login` (local only) · `POST /internal/tick`. Patient actions arrive through the webhook / `POST /simulator/inbound`; there is no
separate endpoint for them. Auth: `Authorization: Bearer <Supabase access token>`; the role
is read from `staff_profile`, never from the token.

## Windows note
Keep Python virtualenvs and any bundled-Postgres tooling out of folders whose names contain non-ASCII characters
(this project folder contains "·"); Postgres `initdb` fails on such paths under a 1252 locale.
