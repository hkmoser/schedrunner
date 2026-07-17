import { test, expect, type Page } from "@playwright/test";

// Drive the real PWA (iPhone viewport) to catch the layout/scroll/click regressions
// jsdom can't see. The dev server serves the public/*-sample.json fixtures.

async function enterPasscode(page: Page, code: string) {
  for (const d of code) await page.locator(".key", { hasText: new RegExp(`^${d}$`) }).click();
}

test.describe("passcode gate", () => {
  test("1937 unlocks the full app", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".keypad")).toBeVisible();
    await enterPasscode(page, "1937");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".appbar-menu")).toBeVisible(); // full app → hamburger present
  });

  test("an all-even code opens the decoy (no menu)", async ({ page }) => {
    await page.goto("/");
    await enterPasscode(page, "2468");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".appbar-menu")).toHaveCount(0); // decoy → no navigation
  });

  test("a wrong code does not unlock", async ({ page }) => {
    await page.goto("/");
    await enterPasscode(page, "1234");
    await page.waitForTimeout(600);
    await expect(page.locator(".app-shell")).toHaveCount(0);
    await expect(page.locator(".keypad")).toBeVisible();
  });
});

test.describe("shell layout", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await enterPasscode(page, "1937");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
  });

  test("the shell fills the viewport — no dead space at the bottom", async ({ page }) => {
    const vp = page.viewportSize()!;
    const box = await page.locator(".app-shell").boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeLessThanOrEqual(1); // starts at the top
    // Bottom edge reaches the viewport bottom (allow 1px rounding).
    expect(box!.y + box!.height).toBeGreaterThanOrEqual(vp.height - 1);
    // The page itself must not scroll (only the inner .scroll does).
    const bodyScrollable = await page.evaluate(() => document.body.scrollHeight > window.innerHeight + 1);
    expect(bodyScrollable).toBeFalsy();
  });

  test("freshness is shown once (page header, not duplicated in the app bar)", async ({ page }) => {
    await expect(page.locator(".appbar-status")).toHaveCount(0);
    await expect(page.getByText(/Updated|ago|Offline/).first()).toBeVisible();
  });
});

test.describe("navigation", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await enterPasscode(page, "1937");
    await expect(page.locator(".app-shell")).toBeVisible({ timeout: 15_000 });
  });

  test("every drawer item is reachable and clickable", async ({ page }) => {
    await page.locator(".appbar-menu").click();
    await expect(page.locator(".drawer")).toBeVisible();
    const items = page.locator(".drawer-item");
    const count = await items.count();
    expect(count).toBeGreaterThanOrEqual(10);
    // Each item — including the lowest ones — scrolls into view and is clickable.
    for (let i = 0; i < count; i++) {
      const it = items.nth(i);
      await it.scrollIntoViewIfNeeded();
      await expect(it).toBeVisible();
    }
    // Tapping the last leaf (Config — near the bottom) navigates and closes the drawer.
    const config = page.locator(".drawer-item", { hasText: "Config" });
    await config.scrollIntoViewIfNeeded();
    await config.click();
    await expect(page.locator(".app-shell.nav-open")).toHaveCount(0);
    await expect(page.getByText("Config").first()).toBeVisible();
  });

  test("a deep nav target (BQ Tables) loads", async ({ page }) => {
    await page.locator(".appbar-menu").click();
    const bq = page.locator(".drawer-item", { hasText: "BQ Tables" });
    await bq.scrollIntoViewIfNeeded();
    await bq.click();
    await expect(page.getByText("BigQuery").first()).toBeVisible();
  });
});
