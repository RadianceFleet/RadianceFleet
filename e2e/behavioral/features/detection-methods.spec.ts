import { test, expect } from '@playwright/test';
import { advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';
import { DetectionsPage } from '../page-objects/DetectionsPage';

test.describe('Detection Methods', () => {
  test('detections page loads with three tabs', async ({ page }) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await expect(dp.spoofingTab).toBeVisible({ timeout: 10_000 });
    await expect(dp.loiteringTab).toBeVisible({ timeout: 10_000 });
    await expect(dp.stsChainsTab).toBeVisible({ timeout: 10_000 });
  });

  test('spoofing tab shows results or empty state', async ({ page }, testInfo) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await dp.spoofingTab.click();
    await page.waitForTimeout(500);

    const hasTable = (await dp.activeTable.count()) > 0;
    const hasEmpty = await dp.emptyState.isVisible().catch(() => false);
    if (!hasTable && !hasEmpty) {
      advisoryReport(testInfo, 'Spoofing tab showed neither table nor empty state');
    }
    expect(hasTable || hasEmpty).toBeTruthy();
  });

  test('loitering tab shows results or empty state', async ({ page }, testInfo) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await dp.loiteringTab.click();
    await page.waitForTimeout(500);

    const hasTable = (await dp.activeTable.count()) > 0;
    const hasEmpty = await dp.emptyState.isVisible().catch(() => false);
    if (!hasTable && !hasEmpty) {
      advisoryReport(testInfo, 'Loitering tab showed neither table nor empty state');
    }
    expect(hasTable || hasEmpty).toBeTruthy();
  });

  test('STS chains tab shows results or empty state', async ({ page }, testInfo) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await dp.stsChainsTab.click();
    await page.waitForTimeout(500);

    const hasTable = (await dp.activeTable.count()) > 0;
    const hasEmpty = await dp.emptyState.isVisible().catch(() => false);
    if (!hasTable && !hasEmpty) {
      advisoryReport(testInfo, 'STS/Chains tab showed neither table nor empty state');
    }
    expect(hasTable || hasEmpty).toBeTruthy();
  });

  test('spoofing results reference documented types', async ({ page }, testInfo) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await dp.spoofingTab.click();
    await page.waitForTimeout(500);

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No spoofing rows available to verify detection types');
      return;
    }

    const knownTypes = [
      'anchor_spoof',
      'circle_spoof',
      'mmsi_reuse',
      'route_laundering',
      'impossible_speed',
    ];

    const tableText = await page.locator('table:visible').textContent();
    const hasKnownType = knownTypes.some((t) => tableText?.toLowerCase().includes(t));
    expect(hasKnownType).toBeTruthy();
  });

  test('detection results have vessel links', async ({ page }, testInfo) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    // Click spoofing tab as a default tab with likely data
    await dp.spoofingTab.click();
    await page.waitForTimeout(500);

    const vesselLinks = page.locator('a[href*="/vessels/"]');
    const linkCount = await vesselLinks.count();

    if (linkCount === 0) {
      advisoryReport(testInfo, 'No vessel links found in detection results');
      return;
    }

    await expect(vesselLinks.first()).toBeVisible({ timeout: 10_000 });
  });

  test('tab switching preserves page state', async ({ page }) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const dp = new DetectionsPage(page);
    await dp.waitForContentLoad();

    await dp.spoofingTab.click();
    await page.waitForTimeout(300);
    await dp.loiteringTab.click();
    await page.waitForTimeout(300);
    await dp.spoofingTab.click();
    await page.waitForTimeout(300);
    await dp.stsChainsTab.click();
    await page.waitForTimeout(300);

    await dp.assertNoErrors();
    expect(page.url()).toContain('/detections');
  });

  test('page has no error boundary', async ({ page }) => {
    await page.goto('/detections', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();
  });
});
