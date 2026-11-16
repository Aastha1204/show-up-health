import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// An iPhone-sized screen. These guard the phone layout: nothing may scroll sideways, the bottom tab bar replaces the top links, and the
// two-doctor schedule shows one doctor at a time.
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

const sideways = (page: import("@playwright/test").Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

test.describe("phone layout (390px wide)", () => {
  test("no page scrolls sideways", async ({ page }) => {
    await signIn(page, "owner");
    for (const path of ["/today", "/waitlist", "/patients", "/inbox", "/import", "/analytics", "/settings", "/simulator", "/demo", "/book"]) {
      await page.goto(path);
      await expect(page.locator("h1").first()).toBeVisible();
      await page.waitForTimeout(800);                                  // let the page's data arrive and lay out
      expect(await sideways(page), `${path} is wider than the screen`).toBeLessThanOrEqual(0);
    }
  });

  test("a bottom tab bar replaces the top links, and More holds the rest", async ({ page }) => {
    await signIn(page, "owner");
    await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
    const bar = page.getByRole("navigation", { name: "Primary" });
    for (const name of ["Today", "Waitlist", "Patients", "Inbox"]) await expect(bar.getByRole("link", { name })).toBeVisible();
    await bar.getByRole("link", { name: "Waitlist" }).click();
    await expect(page).toHaveURL(/\/waitlist/);
    await bar.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    for (const name of ["Import", "Analytics", "Settings", "Simulator", "Demo"]) await expect(sheet.getByRole("link", { name })).toBeVisible();
    await sheet.getByRole("link", { name: "Demo" }).click();
    await expect(page).toHaveURL(/\/demo/);
    await expect(sheet).toBeHidden();                                   // closes itself after navigating
    await bar.getByRole("button", { name: "More" }).click();
    await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
  });

  test("reception's More menu has no owner-only pages", async ({ page }) => {
    await signIn(page, "reception");
    await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "More" });
    await expect(sheet.getByRole("link", { name: "Demo" })).toBeVisible();
    await expect(sheet.getByRole("link", { name: "Settings" })).toHaveCount(0);
    await expect(sheet.getByRole("link", { name: "Analytics" })).toHaveCount(0);
  });

  test("the schedule shows one doctor at a time, switched by tabs", async ({ page }) => {
    await signIn(page, "reception");
    const tabs = page.getByRole("tablist", { name: "Doctor" });
    await expect(tabs.getByRole("tab")).toHaveCount(2);
    await expect(page.locator("[data-doctor]")).toHaveCount(1);
    const first = await page.locator("[data-doctor]").getAttribute("data-doctor");
    await tabs.getByRole("tab", { selected: false }).click();
    await expect(page.locator("[data-doctor]")).toHaveCount(1);
    expect(await page.locator("[data-doctor]").getAttribute("data-doctor")).not.toBe(first);
    expect(await sideways(page)).toBeLessThanOrEqual(0);
  });

  test("tapping an appointment opens a drawer that fits the screen", async ({ page }) => {
    await signIn(page, "reception");
    const card = page.locator("[data-appt]").first();
    await card.scrollIntoViewIfNeeded();
    await card.click();
    const drawer = page.getByRole("complementary");
    await expect(drawer).toBeVisible();
    await page.waitForTimeout(500);                                     // let the slide-in animation finish before measuring
    const box = (await drawer.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390 + 1);
    expect(await sideways(page)).toBeLessThanOrEqual(0);
  });
});
