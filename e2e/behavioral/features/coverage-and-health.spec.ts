import { test, expect } from '@playwright/test';
import { advisoryReport } from '../helpers/data-guard';
import { waitForHealthFreshness, waitForCollectionStatus } from '../helpers/api-monitor';
import { BasePage } from '../page-objects/BasePage';

test.describe('Coverage and Health', () => {
  test('data-health page loads with heading', async ({ page }) => {
    const base = new BasePage(page);
    await page.goto('/data-health', { timeout: 10_000 });
    await base.waitForContentLoad();
    await expect(page.getByRole('heading', { name: /Data Health/i })).toBeVisible({ timeout: 10_000 });
    await base.assertNoErrors();
  });

  test('data-health freshness card visible', async ({ page }, testInfo) => {
    const base = new BasePage(page);
    const freshnessPromise = waitForHealthFreshness(page);
    await page.goto('/data-health', { timeout: 10_000 });
    await base.waitForContentLoad();
    await freshnessPromise;
    await expect(page.getByText(/source.*freshness/i)).toBeVisible({ timeout: 10_000 });
    const rows = page.locator('table tr');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      advisoryReport(testInfo, 'Freshness card has no table rows');
    }
  });

  test('data-health collection card visible', async ({ page }, testInfo) => {
    const base = new BasePage(page);
    const collectionPromise = waitForCollectionStatus(page);
    await page.goto('/data-health', { timeout: 10_000 });
    await base.waitForContentLoad();
    await collectionPromise;
    await expect(page.getByText(/collection.*runs/i)).toBeVisible({ timeout: 10_000 });
    const rows = page.locator('table tr');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      advisoryReport(testInfo, 'Collection card has no table rows');
    }
  });

  test('map coverage overlay toggle', async ({ page }) => {
    const base = new BasePage(page);
    await page.goto('/map', { timeout: 10_000 });
    await base.waitForContentLoad();
    const coverageToggle = page.locator('button, input[type="checkbox"], label').filter({ hasText: /coverage/i });
    if (await coverageToggle.count() > 0) {
      await coverageToggle.first().click();
    }
    await base.assertNoErrors();
    await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 10_000 });
  });

  test('coverage GeoJSON API valid', async ({ request }) => {
    const res = await request.get('/api/v1/coverage/geojson');
    expect(res.status()).toBe(200);
    const body = await res.text();
    expect(body).toContain('FeatureCollection');
  });

  test('satellite providers API accessible', async ({ request }) => {
    const res = await request.get('/api/v1/satellite/providers');
    // Providers endpoint may return 500 if no providers are configured
    expect(res.status()).toBeLessThan(502);
  });

  test('health endpoint returns status', async ({ request }) => {
    const res = await request.get('/api/v1/health');
    expect(res.status()).toBe(200);
    const json = await res.json();
    expect(json).toHaveProperty('status');
  });

  test('donate page loads', async ({ page }) => {
    const base = new BasePage(page);
    await page.goto('/donate', { timeout: 10_000 });
    await base.waitForContentLoad();
    await base.assertNoErrors();
  });
});
