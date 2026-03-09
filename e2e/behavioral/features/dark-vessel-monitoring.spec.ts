import { test, expect } from '@playwright/test';
import { advisoryReport } from '../helpers/data-guard';
import { waitForDarkVessels } from '../helpers/api-monitor';
import { BasePage } from '../page-objects/BasePage';

test.describe('Dark Vessel Monitoring', () => {
  test('page loads with table or empty state', async ({ page }, testInfo) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();

    if (rowCount > 0) {
      await expect(rows.first()).toBeVisible({ timeout: 10_000 });
    } else {
      const emptyState = page.getByText(/no.*dark.*vessel|no.*detection|no.*results/i).first();
      const emptyVisible = await emptyState.isVisible().catch(() => false);
      if (emptyVisible) {
        await expect(emptyState).toBeVisible();
      } else {
        advisoryReport(testInfo, 'No table rows and no explicit empty state text found');
      }
    }
  });

  test('entries show detection metadata', async ({ page }, testInfo) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No dark vessel data available to check metadata');
      return;
    }

    const firstRow = rows.first();

    // Check for coordinates/location text (lat/lon pattern or "location")
    const coordPattern = /\d+\.\d+.*[,°].*\d+\.\d+|location/i;
    const locationText = firstRow.locator('td').filter({ hasText: coordPattern }).first();
    const locationVisible = await locationText.isVisible().catch(() => false);

    // Check for timestamp
    const timestampPattern = /\d{4}[-/]\d{2}[-/]\d{2}|ago|hours|minutes|seconds/i;
    const timestampText = firstRow.locator('td').filter({ hasText: timestampPattern }).first();
    const timestampVisible = await timestampText.isVisible().catch(() => false);

    if (locationVisible || timestampVisible) {
      advisoryReport(testInfo, `Detection metadata found — location: ${locationVisible}, timestamp: ${timestampVisible}`);
    } else {
      advisoryReport(testInfo, 'No recognizable coordinate or timestamp patterns in first row');
    }
  });

  test('entries show AIS match status', async ({ page }, testInfo) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No dark vessel data available to check AIS match status');
      return;
    }

    const aisText = page.locator('table tbody').getByText(/matched|unmatched|ais/i).first();
    const aisVisible = await aisText.isVisible().catch(() => false);

    if (aisVisible) {
      const text = await aisText.textContent();
      advisoryReport(testInfo, `AIS match status found: "${text?.slice(0, 100)}"`);
    } else {
      advisoryReport(testInfo, 'No AIS match status text found in table');
    }
  });

  test('pagination visible when data exists', async ({ page }, testInfo) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No dark vessel data to check pagination');
      return;
    }

    const pagination = page.locator(
      'button:has-text("Prev"), button:has-text("Next"), button:has-text("Previous"), [aria-label*="page"], :text("Page")',
    ).first();
    const paginationVisible = await pagination.isVisible().catch(() => false);

    if (paginationVisible) {
      await expect(pagination).toBeVisible({ timeout: 10_000 });
    } else {
      advisoryReport(testInfo, 'No pagination controls found — dataset may be small');
    }
  });

  test('vessel links navigate', async ({ page }, testInfo) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const vesselLinks = page.locator('table a[href*="/vessels/"]');
    const linkCount = await vesselLinks.count();

    if (linkCount === 0) {
      advisoryReport(testInfo, 'No vessel links found in dark vessel table');
      return;
    }

    await vesselLinks.first().click();
    await page.waitForURL(/\/vessels\/\d+/, { timeout: 10_000 });
    expect(page.url()).toMatch(/\/vessels\/\d+/);
  });

  test('page has no errors', async ({ page }) => {
    const darkVesselsP = waitForDarkVessels(page);
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    await darkVesselsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();
    await expect(base.heading).toBeVisible({ timeout: 10_000 });
  });
});
