import { expect, test } from "@playwright/test";
import { apiAs, disableQuietHours, findUpcoming, openDay, signIn } from "./helpers";

test.describe("phase 8: deposits and calendar feed", () => {
  test("a deposit link is requested from the drawer and shows as paid (simulated payment)", async ({ page, request }) => {
    const api = await apiAs(request);
    disableQuietHours();                                          // the link goes out as a proactive WhatsApp message
    const { appt, date } = await findUpcoming(api, "pending");     // a fresh seed each run: no deposit has been asked for yet
    await signIn(page);
    await openDay(page, date);
    await page.locator(`[data-appt="${appt.id}"]`).click();
    const drawer = page.getByRole("complementary", { name: new RegExp(appt.patient_name) });
    await drawer.getByRole("button", { name: /Request deposit/ }).click();
    await expect(page.getByText(`Deposit link sent to ${appt.patient_name}`)).toBeVisible();
    await expect(drawer.getByTestId("deposit-status")).toContainText("₹500 requested");
    await drawer.getByRole("button", { name: "Simulate: patient paid" }).click();
    await expect(drawer.getByTestId("deposit-status")).toContainText("₹500 paid");
    await expect(drawer.getByRole("button", { name: /deposit/i })).toHaveCount(0);                   // nothing left to ask for
    await drawer.getByRole("tab", { name: "Message log" }).click();
    await expect(drawer.getByText(/booking deposit of ₹500/)).toBeVisible();                           // what the patient actually received
  });

  test("a Hindi-speaking patient gets the reminder in Hindi, with Hindi buttons", async ({ page, request }) => {
    const api = await apiAs(request);
    disableQuietHours();
    const { appt, date } = await findUpcoming(api, "pending", [], "hi");
    expect((await api.post("/demo/fire", { appointment_id: appt.id, type: "day_before" })).ok()).toBeTruthy();
    await signIn(page);
    await openDay(page, date);
    await page.locator(`[data-appt="${appt.id}"]`).click();
    await page.getByRole("tab", { name: "Message log" }).click();
    const log = page.getByRole("list", { name: "Message log" });
    await expect(log.getByText(/आपकी अपॉइंटमेंट है/)).toBeVisible();
    for (const label of ["पुष्टि करें", "समय बदलें", "रद्द करें"]) await expect(log.getByText(label).first()).toBeVisible();
  });

  test("owner copies a private calendar link that serves an .ics through the web proxy", async ({ page, request }) => {
    await signIn(page, "owner");
    await page.goto("/settings");
    await page.getByRole("button", { name: /Dr\. Neha Kulkarni/ }).click();
    const link = page.getByLabel("Calendar link for Dr. Neha Kulkarni");
    const url = await link.inputValue();
    expect(url).toMatch(/\/api\/calendar\/[0-9a-f]{64}\.ics$/);
    const r = await request.get(url);
    expect(r.ok()).toBeTruthy();
    expect(r.headers()["content-type"]).toContain("text/calendar");
    const body = await r.text();
    expect(body).toContain("BEGIN:VCALENDAR");
    expect(body).toContain("BEGIN:VEVENT");
    expect(body).not.toMatch(/\+91\d{10}/);                                                              // no phone numbers in a shared feed
    await page.getByRole("button", { name: "New link…" }).first().click();
    await page.getByRole("alertdialog", { name: "Confirm new calendar link" }).getByRole("button", { name: "Yes, new link" }).click();
    await expect(page.getByText("New calendar link created")).toBeVisible();
    await expect.poll(async () => (await request.get(url)).status()).toBe(404);                           // the old link is dead
  });
});
