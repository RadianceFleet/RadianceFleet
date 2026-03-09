import { test, expect } from '@playwright/test';
import { fetchFirstId, skipIfNoData, advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';

test.describe('Operational Tools', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    vesselId = await fetchFirstId(request, 'vessels', 'vessel_id');
  });

  test('detect page has date inputs and buttons', async ({ page }) => {
    await page.goto('/detect', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const dateInputs = page.locator('input[type="date"]');
    await expect(dateInputs).toHaveCount(2, { timeout: 10_000 });

    const buttons = page.locator('button:visible');
    const buttonCount = await buttons.count();
    expect(buttonCount).toBeGreaterThanOrEqual(3);

    await base.assertNoErrors();
  });

  test('detect buttons are actionable', async ({ page }) => {
    await page.goto('/detect', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const buttons = page.locator('button:visible');
    const buttonCount = await buttons.count();
    expect(buttonCount).toBeGreaterThanOrEqual(1);

    for (let i = 0; i < buttonCount; i++) {
      const btn = buttons.nth(i);
      const isDisabled = await btn.isDisabled();
      if (!isDisabled) {
        // At least one button is enabled — good enough
        return;
      }
    }

    // If we get here, all buttons are disabled — that's unexpected
    expect(false).toBeTruthy();
  });

  test('ingest page has upload area', async ({ page }) => {
    await page.goto('/ingest', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const fileInput = page.locator('input[type="file"]');
    const dropZone = page.locator('[class*="drop"], [class*="upload"], [data-testid*="drop"]');
    const fileInputCount = await fileInput.count();
    const dropZoneCount = await dropZone.count();
    expect(fileInputCount + dropZoneCount).toBeGreaterThanOrEqual(1);

    const bodyText = await page.locator('body').textContent();
    expect(bodyText).toMatch(/upload|CSV|import/i);
  });

  test('admin tips page loads', async ({ page }) => {
    await page.goto('/admin/tips', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    // Tips page uses button-style filter tabs (ALL, PENDING, REVIEWED, etc.)
    const filterButtons = page.locator('button').filter({ hasText: /all|pending|reviewed|actioned|dismissed/i });
    const heading = page.getByText(/tips/i).first();

    const hasFilters = (await filterButtons.count()) > 0;
    const hasHeading = await heading.isVisible().catch(() => false);

    expect(hasFilters || hasHeading).toBeTruthy();

    await base.assertNoErrors();
  });

  test('embed widget renders standalone', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/embed/vessel/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const mmsiPattern = page.getByText(/\d{9}/);
    const vesselName = page.locator('h1, h2, h3, [role="heading"]').first();

    const hasMmsi = (await mmsiPattern.count()) > 0;
    const hasName = await vesselName.isVisible().catch(() => false);

    expect(hasMmsi || hasName).toBeTruthy();
  });

  test('embed widget has no app shell', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/embed/vessel/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const nav = page.locator('nav');
    await expect(nav).not.toBeVisible({ timeout: 10_000 });
  });
});
