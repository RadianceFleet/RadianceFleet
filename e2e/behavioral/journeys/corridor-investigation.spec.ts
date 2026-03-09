import { test, expect } from '@playwright/test';
import { waitForCorridors } from '../helpers/api-monitor';
import { fetchFirstId, skipIfNoData, skipIfEmpty, advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';

test.describe('Corridor Investigation Journey', () => {
  let corridorId: string | null = null;

  test.beforeAll(async ({ request }) => {
    corridorId = await fetchFirstId(request, 'corridors', 'corridor_id');
  });

  test('corridor detail shows activity chart', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const chartHeading = page.getByText(/Activity Over Time|Traffic|Chart/i).first();
    await expect(chartHeading).toBeVisible({ timeout: 10_000 });

    const rechartsSvg = page.locator('.recharts-wrapper svg, svg.recharts-surface').first();
    await expect(rechartsSvg).toBeVisible({ timeout: 10_000 });
  });

  test('corridor alerts table has clickable links', async ({ page }, testInfo) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const alertLinks = page.locator('a[href*="/alerts/"]');
    const count = await alertLinks.count();

    if (count > 0) {
      await expect(alertLinks.first()).toBeVisible({ timeout: 10_000 });
    } else {
      advisoryReport(testInfo, 'No corridor alerts found on detail page');
    }
  });

  test('corridor alert link navigates to alert detail', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const alertLinks = page.locator('a[href*="/alerts/"]');
    await alertLinks.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const count = await alertLinks.count();
    skipIfEmpty(test, count, 'corridor alert links');

    await alertLinks.first().click();
    await page.waitForLoadState('domcontentloaded');
    await base.assertNoErrors();

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    const riskScore = page.getByText(/Risk Score/i).first();
    await expect(riskScore).toBeVisible({ timeout: 10_000 });

    await page.goBack();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/corridors\//);
  });

  test('corridor back link returns to list', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const backLink = page.locator('a[href="/corridors"]').first();
    const backByRole = page.getByRole('link', { name: /Corridors/i }).first();

    const backVisible = await backLink.isVisible().catch(() => false);
    const roleVisible = await backByRole.isVisible().catch(() => false);

    if (backVisible) {
      const corridorsP = waitForCorridors(page);
      await backLink.click();
      await corridorsP;
    } else if (roleVisible) {
      const corridorsP = waitForCorridors(page);
      await backByRole.click();
      await corridorsP;
    } else {
      await page.goBack();
      await page.waitForLoadState('domcontentloaded');
    }

    expect(page.url()).toMatch(/\/corridors$/);
  });

  test('three-level drill: corridor to alert to vessel', async ({ page }, testInfo) => {
    skipIfNoData(test, corridorId, 'corridors');

    // Level 1: Corridor detail
    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const alertLinks = page.locator('a[href*="/alerts/"]');
    await alertLinks.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const alertCount = await alertLinks.count();
    skipIfEmpty(test, alertCount, 'corridor alert links for drill-down');

    // Level 2: Alert detail
    await alertLinks.first().click();
    await page.waitForLoadState('domcontentloaded');
    await base.assertNoErrors();

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    // Level 3: Vessel detail
    const vesselLink = page.locator('a[href*="/vessels/"]').first();
    const vesselVisible = await vesselLink.isVisible({ timeout: 5_000 }).catch(() => false);

    if (vesselVisible) {
      await vesselLink.click();
      await page.waitForLoadState('domcontentloaded');
      await base.assertNoErrors();

      expect(page.url()).toMatch(/\/vessels\/\d+/);

      // Navigate back twice to corridor
      await page.goBack();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).toMatch(/\/alerts\/\d+/);

      await page.goBack();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).toMatch(/\/corridors\//);
    } else {
      advisoryReport(testInfo, 'No vessel link found on alert detail — cannot complete three-level drill');
    }
  });
});
