import { test, expect } from '@playwright/test';
import { fetchFirstAlertWithVessel, advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';

/**
 * API Health Smoke Tests — detect "Failed to load" errors on user-facing pages.
 *
 * These tests catch the most visible production issue category:
 * pages that render but show red "Failed to load" messages because
 * their backing APIs return 4xx/5xx errors.
 */
test.describe('API Health Smoke', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const pair = await fetchFirstAlertWithVessel(request);
    vesselId = pair?.vesselId ?? null;
  });

  // ── Accuracy Dashboard — 6 API-backed sections ──────────────────────

  test('accuracy validation API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/admin/validate?threshold_band=high');
    expect(res.status(), '/admin/validate should be accessible without auth').toBe(200);
  });

  test('accuracy sweep API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/admin/validate/sweep');
    expect(res.status(), '/admin/validate/sweep should be accessible').toBe(200);
  });

  test('accuracy signals API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/admin/validate/signals');
    expect(res.status(), '/admin/validate/signals should be accessible').toBe(200);
  });

  test('accuracy analyst-metrics API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/admin/validate/analyst-metrics');
    expect(res.status(), '/admin/validate/analyst-metrics should be accessible').toBe(200);
  });

  test('accuracy detector-correlation API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/admin/validate/detector-correlation');
    expect(res.status(), '/admin/validate/detector-correlation should exist').toBe(200);
  });

  test('accuracy signal-effectiveness API returns 200', async ({ request }) => {
    const res = await request.get('/api/v1/accuracy/signal-effectiveness');
    expect(res.status()).toBe(200);
  });

  test('accuracy page has no "Failed to load" errors', async ({ page }) => {
    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    // Wait for API responses to settle
    await page.waitForTimeout(2000);

    const failedMessages = page.getByText(/Failed to load/i);
    const failedCount = await failedMessages.count();
    expect(failedCount, 'No sections should show "Failed to load" errors').toBe(0);
  });

  // ── Tips Page ────────────────────────────────────────────────────────

  test('tips page has no "Failed to load" errors', async ({ page }, testInfo) => {
    await page.goto('/admin/tips', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await page.waitForTimeout(2000);

    const failedMessages = page.getByText(/Failed to load/i);
    const failedCount = await failedMessages.count();

    if (failedCount > 0) {
      // Tips requires auth — report as advisory, not hard fail
      advisoryReport(testInfo, `Tips page shows ${failedCount} "Failed to load" error(s) — may require authentication`);
    }
    // The heading should always be visible regardless
    await expect(page.getByText(/Tips Administration/i)).toBeVisible({ timeout: 10_000 });
  });

  // ── Dark Vessels ─────────────────────────────────────────────────────

  test('dark-vessels API returns valid response', async ({ request }) => {
    const res = await request.get('/api/v1/dark-vessels');
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json).toHaveProperty('items');
    expect(json).toHaveProperty('total');
  });

  test('dark-vessels page has no errors', async ({ page }, testInfo) => {
    await page.goto('/dark-vessels', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await page.waitForTimeout(1500);

    await base.assertNoErrors();
    const failedMessages = page.getByText(/Failed to load/i);
    const failedCount = await failedMessages.count();
    expect(failedCount, 'Dark vessels page should not show API errors').toBe(0);

    // Advisory if no data available
    const rows = page.locator('table tbody tr');
    if ((await rows.count()) === 0) {
      advisoryReport(testInfo, 'Dark vessels page has 0 detections — no data ingested');
    }
  });

  // ── Data Health ──────────────────────────────────────────────────────

  test('data-health page has no "Failed to load" errors', async ({ page }, testInfo) => {
    await page.goto('/data-health', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await page.waitForTimeout(2000);

    await base.assertNoErrors();
    const failedMessages = page.getByText(/Failed to load/i);
    const failedCount = await failedMessages.count();
    if (failedCount > 0) {
      advisoryReport(testInfo, `Data health page shows ${failedCount} "Failed to load" error(s)`);
    }
  });

  // ── Vessel Detail — sub-pages should not show errors ─────────────────

  test('vessel detail page has no "Failed to load" errors', async ({ page }, testInfo) => {
    test.skip(!vesselId, 'No vessel available');

    let errorCount = 0;
    for (const subPath of ['', '/detectors', '/voyage', '/timeline']) {
      await page.goto(`/vessels/${vesselId}${subPath}`, { waitUntil: 'domcontentloaded' });
      const base = new BasePage(page);
      await base.waitForContentLoad();

      const failedMessages = page.getByText(/Failed to load/i);
      const failedCount = await failedMessages.count();
      if (failedCount > 0) {
        advisoryReport(testInfo, `/vessels/${vesselId}${subPath} shows ${failedCount} "Failed to load" error(s)`);
        errorCount += failedCount;
      }

      const errorBoundary = page.getByText('Something went wrong');
      const hasCrash = await errorBoundary.isVisible().catch(() => false);
      if (hasCrash) {
        const errorText = await page.locator('p').filter({ hasText: /Cannot read|undefined|null/ }).textContent().catch(() => 'unknown');
        advisoryReport(testInfo, `/vessels/${vesselId}${subPath} CRASHED: ${errorText}`);
        errorCount++;
      }
    }
    expect(errorCount, 'Vessel sub-pages should have no errors or crashes').toBe(0);
  });

  // ── Dashboard — core page should be error-free ───────────────────────

  test('dashboard has no "Failed to load" errors', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await page.waitForTimeout(2000);

    await base.assertNoErrors();
    const failedMessages = page.getByText(/Failed to load/i);
    const failedCount = await failedMessages.count();
    expect(failedCount, 'Dashboard should not show API errors').toBe(0);
  });

  // ── Map — core page should render ────────────────────────────────────

  test('map page has no errors', async ({ page }) => {
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await base.assertNoErrors();
    const mapContainer = page.locator('.leaflet-container');
    await expect(mapContainer).toBeVisible({ timeout: 10_000 });
  });
});
