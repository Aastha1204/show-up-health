import { expect, test } from "@playwright/test";
import { apiAs, disableQuietHours, firstBookableDate, istHHMM, signIn, uniquePhone, openDay, type ApptRow } from "./helpers";

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";

test.describe("analytics (owner only)", () => {
  test("shows the owner the story: tiles, formula, simulated badge, charts and a table view", async ({ page }) => {
    await signIn(page, "owner");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Analytics" }).click();
    await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
    await expect(page.getByTestId("simulated-badge")).toHaveText("Simulated data");               // everything here is seed data
    for (const t of ["No-show rate", "Slots recovered", "Revenue saved", "WhatsApp confirmation rate"]) await expect(page.locator(`[data-tile="${t}"]`)).toBeVisible();
    await expect(page.locator('[data-tile="Revenue saved"]')).toContainText("recovered slots × ₹1,200 average ticket");   // the formula is shown
    await expect(page.locator('[data-tile="Slots recovered"]')).toContainText("18 since reminders went live");
    await expect(page.locator('[data-tile="No-show rate"]')).toContainText(/below baseline/);
    const roi = page.getByTestId("roi-formula");
    await expect(roi).toContainText(/× ₹1,200 = ₹[\d,]+/);
    await expect(roi).toContainText("vs a ₹2,999 plan");
    await expect(page.getByTestId("roi-estimate")).toContainText("estimated");                     // the estimate is labelled as one
    await expect(page.getByRole("img", { name: /Weekly no-show rate, from/ })).toBeVisible();
    await expect(page.getByText("Reminders went live", { exact: true })).toBeVisible();
    await expect(page.getByText(/^Baseline 14%$/)).toBeVisible();
    await expect(page.getByRole("img", { name: /Slots recovered per week; 18 in the last 13 weeks/ })).toBeVisible();
    await page.getByRole("button", { name: "View as table" }).first().click();                     // the accessible alternative to the chart
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByRole("row")).toHaveCount(14);                                           // header + 13 weeks
  });

  test("reception cannot see or open analytics", async ({ page }) => {
    await signIn(page, "reception");
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Analytics" })).toHaveCount(0);
    await page.goto("/analytics");
    await expect(page.getByRole("heading", { name: "Owner access only" })).toBeVisible();
  });
});

test.describe("public booking page", () => {
  test("a visitor can book without logging in, consent is mandatory, and reception sees the booking", async ({ page, request, browser }) => {
    const { date } = await firstBookableDate(request);
    const ctx = await browser.newContext();                                                         // a stranger: no staff token
    const visitor = await ctx.newPage();
    await visitor.goto("/book");
    await expect(visitor.getByRole("heading", { name: "Book an appointment" })).toBeVisible();
    await visitor.getByLabel("Date", { exact: true }).fill(date);
    await expect(visitor.getByRole("radio").first()).toBeVisible();

    await visitor.getByRole("button", { name: "Book appointment" }).click();                       // empty
    await expect(visitor.getByText("Please enter your name")).toBeVisible();
    await expect(visitor.getByText("Enter a 10-digit mobile number starting 6-9")).toBeVisible();
    await expect(visitor.getByText("Please choose a time")).toBeVisible();

    const name = `Web Visitor ${Date.now() % 100000}`;
    await visitor.getByLabel("Your name").fill(name);
    await visitor.getByLabel("Mobile number (WhatsApp)").fill(uniquePhone());
    await visitor.getByRole("radio").first().click();
    await visitor.getByRole("button", { name: "Book appointment" }).click();                       // everything but consent
    await expect(visitor.getByText(/Please tick the box: “I agree to receive appointment updates on WhatsApp.”/)).toBeVisible();
    await expect(visitor.getByTestId("booking-success")).toHaveCount(0);

    await visitor.getByRole("checkbox", { name: /I agree to receive appointment updates on WhatsApp/ }).check();
    await visitor.getByRole("button", { name: "Book appointment" }).click();
    await expect(visitor.getByTestId("booking-success")).toContainText("You're booked");
    await expect(visitor.getByTestId("booking-success")).toContainText("We'll send a confirmation on WhatsApp");
    await ctx.close();

    await signIn(page);
    await openDay(page, date);
    const card = page.locator("[data-appt]", { hasText: name });
    await expect(card).toBeVisible();
    await expect(card.getByTitle("Booked on the website")).toBeVisible();                           // source: website
    await expect(card).toHaveAttribute("data-status", "pending");
  });

  test("the booking form can be embedded, and the staff app cannot be framed", async ({ request, page }) => {
    const embed = await request.get("/book/embed");
    expect(embed.ok()).toBeTruthy();
    expect(embed.headers()["content-security-policy"]).toMatch(/frame-ancestors (\*|'self' https?:)/);   // any site (default) or the configured ones
    for (const path of ["/today", "/login", "/book"]) {
      const r = await request.get(path);
      expect(r.headers()["content-security-policy"], path).toContain("frame-ancestors 'self'");   // clickjacking protection
    }
    await page.goto("/book/embed");
    await expect(page.getByRole("form", { name: "Book an appointment" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);                  // no chrome inside the iframe
    await page.goto("/book");
    await expect(page.getByText("<iframe")).toBeVisible();                                         // the snippet is on the page
  });

  test("a bot that fills the honeypot is told it worked but nothing is booked", async ({ request }) => {
    const { slots, treatmentId } = await firstBookableDate(request);
    const name = `Honeypot Bot ${Date.now() % 100000}`;
    const r = await request.post(`${API}/public/appointments`, { data: { name, phone: "+91" + uniquePhone(), language: "en", doctor_id: null,
      treatment_type_id: treatmentId, start_at: slots[0], consent: true, website: "http://spam.example" } });
    expect(r.status()).toBe(201);
    const api = await apiAs(request);
    expect(await api.get<unknown[]>(`/patients?q=${encodeURIComponent(name)}`)).toHaveLength(0);
  });
});

test.describe("import", () => {
  test("upload a CSV: mapped, validated row by row, consent required, then imported", async ({ page, request }) => {
    const api = await apiAs(request);
    const { date, treatmentId, slots } = await firstBookableDate(request);
    const ref = await api.get<{ doctors: { id: string; name: string }[] }>("/reference");
    const neha = ref.doctors.find((d) => d.name.includes("Neha"))!;
    const free = (await api.get<{ slots: string[] }>(`/slots?doctor_id=${neha.id}&date=${date}&treatment_type_id=${treatmentId}`)).slots;
    expect(free.length).toBeGreaterThan(3);
    const t1 = istHHMM(free[0]), t2 = istHHMM(free[free.length - 1]);
    const [p1, p2] = [uniquePhone(), uniquePhone()];
    const csv = ["Patient,Mobile,Doctor,Date,Time,Service",
      `Import One,${p1},Dr. Neha Kulkarni,${date},${t1},Consultation`,
      `Import Two,${p2},Neha Kulkarni,${date},${t2},Consultation`,
      `Bad Phone,12345,Dr. Neha Kulkarni,${date},${t1},Consultation`,
      `Ghost Doctor,${uniquePhone()},Dr. Nobody,${date},${t1},Consultation`].join("\n");

    await signIn(page);
    await page.goto("/import");
    await page.getByLabel("CSV file").setInputFiles({ name: "appointments.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    const counts = page.getByTestId("import-counts");
    await expect(counts).toContainText("4 rows");
    await expect(counts).toContainText("2 ready");
    await expect(counts).toContainText("2 with problems");
    await expect(page.locator('[data-row="3"]')).toContainText(/Phone: Indian mobile numbers|Phone: Enter a valid/);
    await expect(page.locator('[data-row="4"]')).toContainText("No doctor matches “Dr. Nobody”");
    await expect(page.locator('[data-row="1"]')).toContainText("✓ Ready");
    await expect(page.getByRole("combobox", { name: /^Patient name/ })).toHaveValue("Patient");      // columns were matched automatically

    const go = page.getByRole("button", { name: "Import 2 appointments" });
    await expect(go).toBeDisabled();                                                               // consent not yet confirmed
    await page.getByRole("checkbox", { name: /agreed to receive WhatsApp appointment updates/ }).check();
    await go.click();
    await expect(page.getByTestId("import-result")).toContainText("2 appointments imported");
    await expect(page.getByTestId("import-result")).toContainText("no confirmation message was sent");

    await openDay(page, date);
    for (const n of ["Import One", "Import Two"]) await expect(page.locator("[data-appt]", { hasText: n }).getByTitle("Imported")).toBeVisible();
    const rows = await api.get<ApptRow[]>(`/appointments?date=${date}`);
    expect(rows.filter((a) => a.patient_name.startsWith("Import ")).map((a) => a.status)).toEqual(["pending", "pending"]);

    // importing the same file again creates nothing new
    await page.goto("/import");
    await page.getByLabel("CSV file").setInputFiles({ name: "appointments.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    await expect(page.getByTestId("import-counts")).toContainText("0 ready");
  });

  test("a Google Sheet link must really be a Google Sheet", async ({ page }) => {
    await signIn(page);
    await page.goto("/import");
    await page.getByRole("tab", { name: "Google Sheet link" }).click();
    await page.getByLabel("Google Sheet link").fill("http://169.254.169.254/latest/meta-data");
    await page.getByRole("button", { name: "Load sheet" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Paste a link" })).toContainText("Paste a link from Google Sheets");
  });

  test("columns that cannot be guessed are mapped by hand", async ({ page }) => {
    await signIn(page);
    await page.goto("/import");
    await page.getByLabel("CSV file").setInputFiles({ name: "odd.csv", mimeType: "text/csv", buffer: Buffer.from("Col A,Col B,Col C,Col D,Col E\nAsha,98765 43210,Neha,2099-01-05,10:00\n") });
    await expect(page.getByText("Choose a column for every required field to see the preview.")).toBeVisible();
    for (const [label, col] of [["Patient name", "Col A"], ["Phone", "Col B"], ["Doctor", "Col C"], ["Date", "Col D"], ["Time", "Col E"]])
      await page.getByRole("combobox", { name: new RegExp(`^${label}`) }).selectOption(col);
    await expect(page.getByTestId("import-counts")).toContainText("1 rows");
  });
});

test.describe("the 3-minute demo script", () => {
  test("book the prospect, reschedule on their phone, cancel the seeded slot, book from the waitlist, 18 -> 19 on Analytics", async ({ page, request }) => {
    const api = await apiAs(request, "owner");
    expect((await api.post("/demo/reset")).ok()).toBeTruthy();                                       // known state, whatever ran before
    disableQuietHours();                                                                             // (reset restores the default quiet hours)
    const before = (await api.get<{ tiles: { recovered_since_go_live: number } }>("/analytics")).tiles.recovered_since_go_live;
    expect(before).toBe(18);
    const state = await api.get<{ cancel_me: { patient_name: string; start_at: string; next_waitlist_patient: string }; mode: string }>("/demo/state");
    expect(state.mode).toBe("simulator");
    const cancelMeDate = new Date(state.cancel_me.start_at).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

    await signIn(page, "owner");
    await page.goto("/demo");
    const phone = page.getByRole("region", { name: "WhatsApp Simulator" });
    const schedule = page.getByRole("region", { name: "Live schedule" });
    await expect(page.getByTestId("mode-badge")).toHaveText("Simulator");

    // 1. an appointment for the prospect, booked for tomorrow
    const prospect = `Demo Prospect ${Date.now() % 100000}`;
    await page.getByLabel("Prospect name").fill(prospect);
    await page.getByLabel("Prospect phone").fill(uniquePhone());
    await page.getByRole("button", { name: "Book for tomorrow" }).click();
    await expect(page.getByTestId("selected-appt")).toContainText(prospect);
    await expect(phone.getByText(/is booked for .* with Dr\./)).toBeVisible();                       // confirmation arrived on the phone
    const label = await page.getByTestId("selected-appt").innerText();                              // "Selected: Name · Mon 9 Nov 10:30 AM"
    const day = label.match(/· (\w{3} \d{1,2} \w{3}) /)![1];
    const card = schedule.locator("[data-appt]", { hasText: prospect });
    await expect(card).toHaveAttribute("data-status", "pending");

    // 2. fire the 24-hour reminder; the prospect taps Reschedule and picks a new slot from the list
    await page.getByRole("button", { name: "24-hour reminder" }).click();
    await expect(phone.getByText(/this is a reminder of your appointment/)).toBeVisible();
    await phone.getByRole("button", { name: "Reschedule", exact: true }).click();
    await expect(phone.getByText("Choose a new time for your appointment.")).toBeVisible();
    await phone.getByRole("button", { name: "See slots" }).click();
    const sheet = phone.getByRole("dialog", { name: "Pick a new time" });
    await expect(sheet).toBeVisible();
    await sheet.getByRole("button", { name: new RegExp(`^${day} · `) }).last().click();            // a time on the same day, so the card stays in view
    await sheet.getByRole("button", { name: "Send" }).click();
    await expect(phone.getByText(/is booked for .* with Dr\./).last()).toBeVisible();              // a fresh confirmation

    // 3. the dashboard updated live in front of them: the card turned blue (rescheduled) within 5 s
    await expect(card).toHaveAttribute("data-status", "rescheduled", { timeout: 5_000 });

    // 4. cancel the seeded appointment: the offer goes out to the waitlisted patient
    await expect(page.getByText(new RegExp(`Next on the waitlist: ${state.cancel_me.next_waitlist_patient}`))).toBeVisible();
    await page.getByRole("button", { name: "Cancel it & offer the slot" }).click();
    await expect(page.getByText(new RegExp(`${state.cancel_me.patient_name} cancelled. The slot was offered to ${state.cancel_me.next_waitlist_patient}`))).toBeVisible();
    await expect(phone.getByText(/a slot has opened at/)).toBeVisible();                            // in the waitlisted patient's chat
    await expect(phone.getByRole("button", { name: "Not now" })).toBeVisible();

    // ...who taps Book: the gap is filled
    await phone.getByRole("button", { name: "Book", exact: true }).click();
    await expect(phone.getByText(/is booked for .* with Dr\./).last()).toBeVisible();
    await schedule.getByLabel("Date").fill(cancelMeDate);
    const filled = schedule.locator("[data-appt]", { hasText: state.cancel_me.next_waitlist_patient });
    await expect(filled).toHaveAttribute("data-status", "confirmed");
    await expect(filled.getByTitle("Booked from the waitlist")).toBeVisible();
    await expect(schedule.locator("[data-appt]", { hasText: state.cancel_me.patient_name })).toHaveAttribute("data-status", "cancelled");

    // 5. close on analytics: recovered slots 18 -> 19, and what it is worth
    await page.getByRole("link", { name: "Analytics →" }).click();
    await expect(page.locator('[data-tile="Slots recovered"]')).toContainText("19 since reminders went live");
    await expect(page.getByTestId("roi-formula")).toContainText("vs a ₹2,999 plan");
    expect((await api.get<{ tiles: { recovered_since_go_live: number } }>("/analytics")).tiles.recovered_since_go_live).toBe(19);
  });

  test("controls: offer lifetime, doctor summary, Live refused when not set up, and reset restores the demo", async ({ page, request }) => {
    await signIn(page, "owner");
    await page.goto("/demo");
    await page.getByRole("button", { name: "2 min" }).click();
    await expect(page.getByRole("button", { name: "2 min" })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "15 min" }).click();
    await expect(page.getByRole("button", { name: "15 min" })).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: "Send 8 a.m. summary" }).click();
    await expect(page.getByRole("region", { name: "WhatsApp Simulator" }).getByText(/Good morning Dr\./)).toBeVisible();   // the doctor's chat

    await page.getByRole("group", { name: "Messaging mode" }).getByRole("button", { name: "Live" }).click();
    await expect(page.getByText(/Live WhatsApp is not available/)).toBeVisible();                   // refused, and it says why
    await expect(page.getByTestId("mode-badge")).toHaveText("Simulator");
    await page.getByRole("button", { name: "Simulator" }).first().click();                          // the fallback is always one click

    await page.getByRole("button", { name: "Reset demo" }).click();
    await page.getByRole("button", { name: "Yes, reset" }).click();
    await expect(page.getByText("Demo reset: fresh data for today")).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel it & offer the slot" })).toBeVisible();   // the seeded appointment is back
    const api = await apiAs(request, "owner");
    expect((await api.get<{ tiles: { recovered_since_go_live: number } }>("/analytics")).tiles.recovered_since_go_live).toBe(18);
  });
});
