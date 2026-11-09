import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// Runs last (alphabetical): it edits clinic settings, and each run starts from a fresh seed.
test.describe("settings (owner only)", () => {
  test("reception cannot see or open Settings", async ({ page }) => {
    await signIn(page, "reception");
    await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Settings" })).toHaveCount(0);
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Owner access only" })).toBeVisible();
  });

  test("WhatsApp: the health check says what is missing and Live is refused until it is ready", async ({ page }) => {
    await signIn(page, "owner");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Settings" }).click();
    const wa = page.getByRole("region", { name: "WhatsApp" });
    await expect(wa.getByText("Not ready for Live")).toBeVisible();
    await expect(wa.getByRole("alert").filter({ hasText: "WA_ACCESS_TOKEN" })).toBeVisible();
    await expect(wa.getByText("Last webhook received")).toBeVisible();
    await expect(wa.getByRole("button", { name: "Simulator" })).toHaveAttribute("aria-pressed", "true");
    await wa.getByRole("group", { name: "Messaging mode" }).getByRole("button", { name: "Live" }).click();
    await expect(page.getByText(/Live WhatsApp is not ready/)).toBeVisible();                       // refused, with the reason
    await expect(wa.getByRole("button", { name: "Simulator" })).toHaveAttribute("aria-pressed", "true");
    await expect(wa.getByRole("button", { name: /Submit templates to Meta/ })).toHaveCount(0);        // needs credentials first
  });

  test("clinic details save, persist, and refuse text that would block every message", async ({ page }) => {
    await signIn(page, "owner");
    await page.goto("/settings");
    const card = page.getByRole("region", { name: "Clinic details" });
    const ticket = card.getByLabel("Average ticket");
    await expect(ticket).toHaveValue("1200");
    await ticket.fill("1500");
    await card.getByRole("button", { name: "Save clinic details" }).click();
    await expect(page.getByText("Clinic details saved")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Clinic details" }).getByLabel("Average ticket")).toHaveValue("1500");
    await page.getByRole("region", { name: "Clinic details" }).getByLabel("Average ticket").fill("1200");   // put it back
    await page.getByRole("region", { name: "Clinic details" }).getByRole("button", { name: "Save clinic details" }).click();
    await expect(page.getByText("Clinic details saved").last()).toBeVisible();

    const name = page.getByRole("region", { name: "Clinic details" }).getByLabel("Clinic name");
    const before = await name.inputValue();
    await name.fill("Root Canal Specialists");
    await page.getByRole("region", { name: "Clinic details" }).getByRole("button", { name: "Save clinic details" }).click();
    await expect(page.getByText(/never mention treatments or medical terms/)).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Clinic details" }).getByLabel("Clinic name")).toHaveValue(before);   // nothing was saved
  });

  test("reminders: a switched-off reminder stays off after reload", async ({ page }) => {
    await signIn(page, "owner");
    await page.goto("/settings");
    const card = () => page.getByRole("region", { name: "Reminders and quiet hours" });
    const box = () => card().getByRole("checkbox", { name: /Reminder the day before/ });
    await expect(box()).toBeChecked();
    await box().uncheck();
    await card().getByRole("button", { name: "Save reminder settings" }).click();
    await expect(page.getByText("Reminder settings saved")).toBeVisible();
    await page.reload();
    await expect(box()).not.toBeChecked();
    await box().check();                                                                              // restore
    await card().getByRole("button", { name: "Save reminder settings" }).click();
    await expect(page.getByText("Reminder settings saved").last()).toBeVisible();
  });

  test("doctors and treatments: add, reject duplicates, deactivate", async ({ page }) => {
    await signIn(page, "owner");
    await page.goto("/settings");
    const docs = page.getByRole("region", { name: "Doctors and working hours" });
    const fresh = docs.locator('[data-doctor-editor="new"]');
    await fresh.getByLabel("Name").fill("Dr. E2E Temp");
    await fresh.getByRole("button", { name: "Add doctor" }).click();
    await expect(page.getByText("Dr. E2E Temp added")).toBeVisible();
    const row = docs.locator('[data-doctor-editor="Dr. E2E Temp"]');
    await expect(row).toBeVisible();
    await docs.locator('[data-doctor-editor="new"]').getByLabel("Name").fill("dr. e2e temp");              // same name, different case
    await docs.locator('[data-doctor-editor="new"]').getByRole("button", { name: "Add doctor" }).click();
    await expect(page.getByText(/already a doctor called/)).toBeVisible();
    await row.getByRole("button", { name: "Deactivate" }).click();
    await expect(page.getByText("Dr. E2E Temp deactivated")).toBeVisible();
    await expect(row.getByText("(inactive)")).toBeVisible();

    const tr = page.getByRole("region", { name: "Treatments" });
    const add = tr.locator('[data-treatment="new"]');
    await add.getByLabel("New treatment").fill("E2E Whitening");
    await add.getByLabel("Minutes").fill("45");
    await add.getByRole("button", { name: "Add" }).click();
    await expect(page.getByText("E2E Whitening added")).toBeVisible();
    await expect(tr.locator('[data-treatment="E2E Whitening"]')).toBeVisible();
    await tr.locator('[data-treatment="new"]').getByLabel("New treatment").fill("consultation");
    await tr.locator('[data-treatment="new"]').getByRole("button", { name: "Add" }).click();
    await expect(page.getByText(/already a treatment called/)).toBeVisible();
  });

  test("template preview shows all nine and flags Hindi/Marathi as drafts", async ({ page }) => {
    await signIn(page, "owner");
    await page.goto("/settings");
    const t = page.getByRole("region", { name: "Message templates" });
    await expect(t.locator("[data-template]")).toHaveCount(9);
    await expect(t.locator('[data-template="showup_day_before"]')).toContainText("this is a reminder of your appointment");
    await expect(t.locator('[data-template="showup_day_before"]').getByText("Reschedule")).toBeVisible();
    await t.getByLabel("Language").selectOption("hi");
    await expect(t.locator('[data-template="showup_day_before"]')).toContainText("पुष्टि करें");       // the Hindi button label
    await expect(t.locator("[data-translation-note]")).toContainText("first drafts");
    await t.getByLabel("Language").selectOption("mr");
    await expect(t.locator('[data-template="showup_day_before"]')).toContainText("पुष्टी करा");
  });
});
