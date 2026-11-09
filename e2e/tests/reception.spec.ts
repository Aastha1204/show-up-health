import { expect, test } from "@playwright/test";
import { apiAs, closeWindow, disableQuietHours, findCancelMe, findUpcoming, openDay, signIn, type ApptRow } from "./helpers";

test.describe("access", () => {
  test("signed-out visitors are sent to login, and reception sees no Settings or Analytics", async ({ page }) => {
    await page.goto("/today");
    await expect(page).toHaveURL(/\/login/);
    await signIn(page, "reception");
    const nav = page.getByRole("navigation", { name: "Main" });
    for (const name of ["Today", "Waitlist", "Patients", "Inbox", "Simulator"]) await expect(nav.getByRole("link", { name })).toBeVisible();
    await expect(nav.getByRole("link", { name: /analytics|settings/i })).toHaveCount(0);
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/patients");
    await expect(page).toHaveURL(/\/login/);          // the session really ended
  });
});

test.describe("Today view", () => {
  test("shows the day: cards, open slots and counts that agree with the data", async ({ page, request }) => {
    const api = await apiAs(request);
    const { date } = await findUpcoming(api, "pending");
    const rows = await api.get<ApptRow[]>(`/appointments?date=${date}`);
    await signIn(page);
    await openDay(page, date);
    await expect(page.locator("[data-appt]").first()).toBeVisible();
    expect(await page.locator("[data-appt]").count()).toBe(rows.length);
    await expect(page.getByRole("button", { name: /^Open slot/ }).first()).toBeVisible();
    await expect(page.getByText(/Closed · /).first()).toBeVisible();                 // the break between sessions
    const total = rows.filter((a) => a.status !== "cancelled").length;
    await expect(page.locator("[data-count=total] div").first()).toHaveText(String(total));
    await expect(page.locator("[data-count=pending] div").first()).toHaveText(String(rows.filter((a) => a.status === "pending").length));
    // every card carries only the last 4 digits of the phone
    const card = page.locator("[data-appt]").first();
    await expect(card).toContainText(/•{4} \d{4}/);
    await expect(card).not.toContainText("+91");
  });

  test("a patient's WhatsApp Confirm turns the card green within 5 s, with a toast (no refresh)", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt, date } = await findUpcoming(api, "pending");
    await signIn(page);
    await openDay(page, date);
    const card = page.locator(`[data-appt="${appt.id}"]`);
    await expect(card).toHaveAttribute("data-status", "pending");
    expect((await api.tap(appt.patient_phone, `CONFIRM:${appt.id}`, "Confirm")).ok()).toBeTruthy();
    await expect(card).toHaveAttribute("data-status", "confirmed", { timeout: 5_000 });
    await expect(page.getByText(`${appt.patient_name} confirmed via WhatsApp · just now`)).toBeVisible();
  });

  test("a patient's Cancel shows red on the card within 5 s", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt, date } = await findUpcoming(api, "confirmed");
    await signIn(page);
    await openDay(page, date);
    const card = page.locator(`[data-appt="${appt.id}"]`);
    await expect(card).toHaveAttribute("data-status", "confirmed");
    await api.tap(appt.patient_phone, `CANCEL:${appt.id}`, "Cancel");
    await expect(card).toHaveAttribute("data-status", "cancelled", { timeout: 5_000 });
    await expect(page.getByText(`${appt.patient_name} cancelled via WhatsApp · just now`)).toBeVisible();
    await expect(card.locator(".name")).toHaveCSS("text-decoration-line", "line-through");
  });

  test("call list: 'Called — confirmed' confirms the patient and drops them from the list", async ({ page, request }) => {
    const api = await apiAs(request);
    const list = await api.get<ApptRow[]>("/appointments/call-list");
    test.skip(list.length === 0, "nothing starts in the next 12 hours right now (Sunday / after hours): the panel is legitimately empty");
    await signIn(page);
    await page.goto("/today");
    const panel = page.getByRole("region", { name: "Call these patients" });
    await expect(panel.getByRole("link", { name: /^Call / }).first()).toHaveAttribute("href", /^tel:\+91/);
    const first = panel.locator("li").first();
    const name = (await first.locator(".font-semibold").first().innerText()).trim();
    await first.getByRole("button", { name: "Called — confirmed" }).click();
    await expect(page.getByText(`${name} confirmed by phone`)).toBeVisible();
    await expect(panel.getByText(name, { exact: true })).toHaveCount(0);
  });

  test("reception cancelling a card offers the slot to the waitlist, with a live countdown", async ({ page, request }) => {
    const api = await apiAs(request);
    expect((await api.post("/demo/reset")).ok()).toBeTruthy();       // the seeded appointment may have been used by an earlier test
    disableQuietHours();                                              // offers are proactive messages: not sent during quiet hours
    const { appt, date } = await findCancelMe(api);
    await signIn(page);
    await openDay(page, date);
    await page.locator(`[data-appt="${appt.id}"]`).click();
    const drawer = page.getByRole("complementary", { name: new RegExp(appt.patient_name) });
    await expect(drawer.getByText("DEMO: cancel me")).toBeVisible();
    await drawer.getByRole("button", { name: "Cancel appointment" }).click();
    await drawer.getByRole("button", { name: "Yes, cancel it" }).click();
    await expect(page.locator(`[data-appt="${appt.id}"]`)).toHaveAttribute("data-status", "cancelled");
    await page.getByRole("link", { name: "Waitlist" }).click();
    const offered = page.locator("[data-entry]", { hasText: "Offer sent" });
    await expect(offered).toHaveCount(1);
    await expect(offered.getByText(/Expires in/)).toBeVisible();
    await expect(offered.getByLabel(/expires in \d+:\d\d/)).toBeVisible();
  });

  test("message log shows what was sent and what the patient did, in order", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt, date } = await findUpcoming(api, "pending", []);
    await api.post("/demo/fire", { appointment_id: appt.id, type: "day_before" });
    await api.tap(appt.patient_phone, `RESCHEDULE:${appt.id}`, "Reschedule");
    await signIn(page);
    await openDay(page, date);
    await page.locator(`[data-appt="${appt.id}"]`).click();
    await page.getByRole("tab", { name: "Message log" }).click();
    const log = page.getByRole("list", { name: "Message log" });
    await expect(log.getByText(/this is a reminder of your appointment/)).toBeVisible();
    await expect(log.getByText("Simulator").first()).toBeVisible();
    await expect(log.getByText(/✓ Sent/).first()).toBeVisible();
    await expect(log.getByText("“Reschedule”")).toBeVisible();
    await expect(log.getByText("slot list")).toBeVisible();                           // the list that tap triggered
  });

  test("running late: preview and the Send button follow the recipient count", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: "Running late" }).click();
    const dlg = page.getByRole("dialog", { name: "Running late" });
    await dlg.getByRole("radio", { name: "45 min" }).click();
    await expect(dlg.getByText(/running about 45 minutes late today/)).toBeVisible();
    const count = Number((await dlg.getByText(/will be messaged/).locator("b").innerText()).trim());
    const send = dlg.getByRole("button", { name: /^Send to/ });
    if (count === 0) await expect(send).toBeDisabled(); else await expect(send).toBeEnabled();
  });
});

test.describe("add / edit appointment", () => {
  test("validates, shows only free slots, and reports a lost race clearly", async ({ page, request }) => {
    const api = await apiAs(request);
    const { date } = await findUpcoming(api, "pending");
    await signIn(page);
    await openDay(page, date);
    await page.getByRole("button", { name: "+ Add appointment" }).click();
    const dlg = page.getByRole("dialog", { name: "Add appointment" });

    await dlg.getByRole("button", { name: "Book appointment" }).click();
    await expect(dlg.getByText("Choose a patient, or add a new one")).toBeVisible();

    await dlg.getByRole("button", { name: "+ Add a new patient" }).click();
    await dlg.getByLabel("Name").fill("E2E Patient");
    await dlg.getByLabel("Mobile number").fill("12345");
    await dlg.getByRole("button", { name: "Book appointment" }).click();
    await expect(dlg.getByText("Enter a 10-digit Indian mobile number starting 6-9")).toBeVisible();
    await expect(dlg.getByText("Required: how did the patient agree to WhatsApp updates?")).toBeVisible();

    const unique = () => "9" + String(Math.floor(Math.random() * 1e9)).padStart(9, "0");
    await dlg.getByLabel("Mobile number").fill(unique());
    await dlg.getByLabel("Consent given via").selectOption("reception");
    await dlg.getByLabel("Date", { exact: true }).fill(date);
    const slots = dlg.getByRole("radio");
    await expect(slots.first()).toBeVisible();
    await slots.first().click();
    const slot = await dlg.locator("[role=radio][aria-checked=true]").getAttribute("data-slot");
    expect(slot).toBeTruthy();

    // someone else takes that very time while this form is open
    const taken = await api.post("/appointments", {
      new_patient: { name: "E2E Racer", phone: "+91" + unique(), language: "en", consent_source: "reception" },
      doctor_id: await dlg.getByLabel("Doctor").inputValue(), treatment_type_id: await dlg.getByLabel("Treatment").inputValue(), start_at: slot,
    });
    expect(taken.status()).toBe(201);

    await dlg.getByRole("button", { name: "Book appointment" }).click();
    await expect(dlg.getByRole("alert").filter({ hasText: "just taken by another booking" })).toBeVisible();
    await expect(dlg.locator(`[data-slot="${slot}"]`)).toHaveCount(0);                     // and the slot is gone from the list

    await slots.first().click();
    await dlg.getByRole("button", { name: "Book appointment" }).click();
    await expect(page.getByText(/^Booked for /)).toBeVisible();
    await expect(dlg).toHaveCount(0);
    await expect(page.locator("[data-appt]", { hasText: "E2E Patient" })).toBeVisible();
  });

  test("rescheduling from the drawer moves the card and marks it rescheduled", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt, date } = await findUpcoming(api, "pending");
    await signIn(page);
    await openDay(page, date);
    await page.locator(`[data-appt="${appt.id}"]`).click();
    await page.getByRole("complementary").getByRole("button", { name: "Reschedule" }).click();
    const dlg = page.getByRole("dialog", { name: new RegExp(`Reschedule · ${appt.patient_name}`) });
    await expect(dlg.getByText("current", { exact: true })).toBeVisible();                   // own slot is offered as 'current'
    const choice = dlg.getByRole("radio").filter({ hasNotText: "current" }).first();
    await choice.click();
    await dlg.getByRole("button", { name: "Move appointment" }).click();
    await expect(page.getByText(/^Moved to /)).toBeVisible();
    await expect(page.locator(`[data-appt="${appt.id}"]`)).toHaveAttribute("data-status", "rescheduled");
  });
});

test.describe("waitlist, patients, inbox", () => {
  test("waitlist: add an entry and remove it", async ({ page }) => {
    await signIn(page);
    await page.goto("/waitlist");
    await page.getByRole("button", { name: "+ Add to waitlist" }).click();
    const dlg = page.getByRole("dialog", { name: "Add to waitlist" });
    await dlg.getByRole("button", { name: "+ Add a new patient" }).click();
    await dlg.getByLabel("Name").fill("E2E Waiter");
    await dlg.getByLabel("Mobile number").fill("9" + String(Math.floor(Math.random() * 1e9)).padStart(9, "0"));
    await dlg.getByLabel("Consent given via").selectOption("reception");
    await dlg.getByRole("button", { name: "Add to waitlist" }).click();
    const entry = page.locator("[data-entry]", { hasText: "E2E Waiter" });
    await expect(entry).toBeVisible();
    await expect(entry).toContainText("Any doctor");
    await entry.getByRole("button", { name: "Remove" }).click();
    await entry.getByRole("button", { name: "Yes, remove" }).click();
    await expect(page.locator("[data-entry]", { hasText: "E2E Waiter" })).toHaveCount(0);
  });

  test("patients: search, consent and seeded badge", async ({ page, request }) => {
    const api = await apiAs(request);
    const [first] = await api.get<{ name: string }[]>("/patients?limit=1");
    await signIn(page);
    await page.goto("/patients");
    await page.getByLabel("Search patients").fill(first.name);
    const row = page.locator("[data-patient]", { hasText: first.name }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("Seeded");
    await expect(row).toContainText(/Active|Opted out/);
    await expect(page.getByRole("columnheader", { name: "Consent" })).toBeVisible();
  });

  test("inbox: a typed message appears, is answered inside the window, and is handled", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt } = await findUpcoming(api, "confirmed");
    const text = `Can I come 15 minutes earlier? (${Date.now()})`;
    await api.say(appt.patient_phone, text);
    await signIn(page);
    await page.goto("/inbox");
    const item = page.locator("[data-inbox]", { hasText: text });
    await expect(item).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" }).getByLabel(/unhandled/)).toBeVisible();   // unread badge
    await item.getByLabel(`Reply to ${appt.patient_name}`).fill("Yes, that works. See you then.");
    await item.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText(`Reply sent to ${appt.patient_name}`)).toBeVisible();
    await expect(page.locator("[data-inbox]", { hasText: text })).toHaveCount(0);
    await page.getByRole("tab", { name: "Handled" }).click();
    await expect(page.locator("[data-inbox]", { hasText: text })).toBeVisible();
  });

  test("inbox: the reply box is disabled, with the reason, when the 24h window is closed", async ({ page, request }) => {
    const api = await apiAs(request);
    const { appt } = await findUpcoming(api, "confirmed");
    await api.tap(appt.patient_phone, `RS_NONE:${appt.id}`, "None of these work");        // creates an inbox item for reception
    closeWindow(appt.patient_phone);                                                       // ...but their last message is now 25 h old
    await signIn(page);
    await page.goto("/inbox");
    const item = page.locator("[data-inbox]", { hasText: "None of the offered reschedule times work" }).first();
    await expect(item).toBeVisible();
    await expect(item.getByLabel(`Reply to ${appt.patient_name}`)).toBeDisabled();
    await expect(item.getByRole("button", { name: "Send" })).toBeDisabled();
    await expect(item.getByText(/24-hour WhatsApp window is closed/)).toBeVisible();     // the reason is visible, not only a tooltip
    await expect(item.getByRole("button", { name: "Send" })).toHaveAttribute("title", /24-hour WhatsApp window is closed/);
  });
});
