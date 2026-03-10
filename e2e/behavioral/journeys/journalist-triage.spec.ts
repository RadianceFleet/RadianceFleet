import { test, expect } from '@playwright/test';
import { waitForAlerts, waitForStats } from '../helpers/api-monitor';
import { fetchFirstAlertWithVessel, fetchMultipleIds, skipIfNoData, advisoryReport } from '../helpers/data-guard';
import { AlertListPage } from '../page-objects/AlertListPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('Journalist Triage Journey', () => {
  let pair: { alertId: string; vesselId: string } | null = null;
  let alertIds: string[] = [];

  test.beforeAll(async ({ request }) => {
    pair = await fetchFirstAlertWithVessel(request);
    alertIds = await fetchMultipleIds(request, 'alerts', 'gap_event_id', 2);
  });

  test('dashboard "View All Alerts" leads to alert list', async ({ page }) => {
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await statsP;

    const viewAllLink = page.getByRole('link', { name: 'View All Alerts' });
    await expect(viewAllLink).toBeVisible({ timeout: 10_000 });

    const alertsP = waitForAlerts(page);
    await viewAllLink.click();
    await alertsP;

    expect(page.url()).toMatch(/\/alerts/);
  });

  test('alert list to alert detail full journey', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const firstLink = alp.alertLinks.first();
    await expect(firstLink).toBeVisible({ timeout: 10_000 });
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    const riskScore = page.getByText(/Risk Score/i).first();
    await expect(riskScore).toBeVisible({ timeout: 10_000 });

    const gapDetails = page.getByText(/Gap Details/i).first();
    await expect(gapDetails).toBeVisible({ timeout: 10_000 });
  });

  test('alert detail vessel link loads profile', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const vesselLink = page.locator('a[href*="/vessels/"]').first();
    await expect(vesselLink).toBeVisible({ timeout: 10_000 });
    await vesselLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/vessels\/\d+/);

    const main = page.locator('main');
    const mmsiOrName = main.getByText(/\d{9}|[A-Z]/).first();
    await expect(mmsiOrName).toBeVisible({ timeout: 10_000 });
  });

  test('vessel sub-pages accessible via direct navigation', async ({ page }) => {
    skipIfNoData(test, pair?.vesselId ?? null, 'vessel');

    const base = new BasePage(page);

    await page.goto(`/vessels/${pair!.vesselId}/detectors`, { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await base.assertNoErrors();

    await page.goto(`/vessels/${pair!.vesselId}/voyage`, { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await base.assertNoErrors();

    await page.goto(`/vessels/${pair!.vesselId}/timeline`, { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await base.assertNoErrors();
  });

  test('vessel "Recent Gap Alerts" links back to alert detail', async ({ page }, testInfo) => {
    skipIfNoData(test, pair?.vesselId ?? null, 'vessel');

    await page.goto(`/vessels/${pair!.vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const alertLinksInSection = page.locator('a[href*="/alerts/"]');
    const count = await alertLinksInSection.count();

    if (count > 0) {
      await alertLinksInSection.first().click();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).toMatch(/\/alerts\/\d+/);
    } else {
      advisoryReport(testInfo, 'No "Recent Gap Alerts" links found on vessel profile');
    }
  });

  test('return to alert list after investigation', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const firstLink = alp.alertLinks.first();
    await expect(firstLink).toBeVisible({ timeout: 10_000 });
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    // Browser back — SPA may restore from cache without re-fetching
    await page.goBack();
    await page.waitForURL(/\/alerts$/, { timeout: 10_000 });

    expect(page.url()).toMatch(/\/alerts/);
  });

  test('consecutive alert investigation (two alerts)', async ({ page }) => {
    test.skip(alertIds.length < 2, 'Need at least 2 alerts for consecutive investigation');

    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const base = new BasePage(page);

    // Click first alert
    const firstLink = alp.alertLinks.first();
    await expect(firstLink).toBeVisible({ timeout: 10_000 });
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);
    const firstUrl = page.url();
    await base.assertNoErrors();

    // Go back to list — SPA may restore from cache without re-fetching
    await page.goBack();
    await page.waitForURL(/\/alerts$/, { timeout: 10_000 });

    // Click second alert
    const secondLink = alp.alertLinks.nth(1);
    await expect(secondLink).toBeVisible({ timeout: 10_000 });
    await secondLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);
    expect(page.url()).not.toBe(firstUrl);
    await base.assertNoErrors();
  });
});
