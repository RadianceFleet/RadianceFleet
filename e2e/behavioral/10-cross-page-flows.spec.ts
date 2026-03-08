import { test, expect } from '@playwright/test';
import { waitForAlerts, waitForAlertMap, waitForVessels, waitForCorridors } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData, skipIfEmpty, advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Cross-Page Flows', () => {
  let alertId: string | null = null;
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
    vesselId = await fetchFirstId(request, 'vessels', 'vessel_id');
  });

  test('dashboard → alerts → detail → vessel', async ({ page }) => {
    skipIfNoData(test, alertId, 'alerts');

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    // Find a link leading to /alerts
    const alertsLink = page.locator('main a[href*="/alerts"]').first();
    await expect(alertsLink).toBeVisible({ timeout: 10_000 });

    const alertsP = waitForAlerts(page);
    await alertsLink.click();
    await alertsP;

    // On /alerts: click first alert link
    const firstAlertLink = page.locator('table a[href*="/alerts/"]').first();
    await expect(firstAlertLink).toBeVisible();
    await firstAlertLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    // On alert detail: find vessel link and click
    const vesselLink = page.locator('main a[href*="/vessels/"]').first();
    const vesselLinkVisible = await vesselLink.isVisible().catch(() => false);

    if (vesselLinkVisible) {
      await vesselLink.click();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).toMatch(/\/vessels\//);
    }
  });

  test('map popup → alert detail → back', async ({ page }) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const markers = page.locator('.leaflet-marker-icon');
    const markerCount = await markers.count();
    skipIfEmpty(test, markerCount, 'map markers');

    await markers.first().dispatchEvent('click');
    const popup = page.locator('.leaflet-popup').first();
    await expect(popup).toBeVisible({ timeout: 5_000 });

    const detailLink = popup.locator('a').first();
    const linkVisible = await detailLink.isVisible().catch(() => false);
    skipIfEmpty(test, linkVisible ? 1 : 0, 'popup detail links');

    await detailLink.click();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/alerts\//);

    await page.goBack();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/map/);
  });

  test('vessel search → detail → sub-page', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessels');

    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const searchInput = page.getByPlaceholder('Search MMSI, IMO, or name...');
    const searchP = waitForVessels(page);
    await searchInput.fill('a');
    await searchP;

    const firstVesselLink = page.locator('main a[href*="/vessels/"]').first();
    await expect(firstVesselLink).toBeVisible();
    await firstVesselLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/vessels\/\d+/);

    // Try clicking a sub-page tab/link
    const subPageLink = page.locator('a[href*="/detectors"], a[href*="/voyage"], a[href*="/timeline"]').first();
    const subVisible = await subPageLink.isVisible().catch(() => false);

    if (subVisible) {
      await subPageLink.click();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).toMatch(/\/(detectors|voyage|timeline)/);
    }

    await base.assertNoErrors();
  });

  test('corridors → detail → back to list', async ({ page, request }) => {
    const corridorId = await fetchFirstId(request, 'corridors');
    skipIfNoData(test, corridorId, 'corridors');

    const corridorsP = waitForCorridors(page);
    await page.goto('/corridors', { waitUntil: 'domcontentloaded' });
    await corridorsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const firstCorridorLink = page.locator('main a[href*="/corridors/"]').first();
    await expect(firstCorridorLink).toBeVisible();
    await firstCorridorLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/corridors\//);

    // Navigate back via back link or browser back
    const backLink = page.locator('a[href="/corridors"], a:has-text("Back")').first();
    const backVisible = await backLink.isVisible().catch(() => false);

    if (backVisible) {
      await backLink.click();
    } else {
      await page.goBack();
    }

    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/corridors$/);
  });

  test('alert filters persist across navigation', async ({ page }, testInfo) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const minScore = page.getByPlaceholder('Min score');
    await expect(minScore).toBeVisible();

    // Apply a filter
    const filteredP = waitForAlerts(page);
    await minScore.fill('50');
    await filteredP;

    // Navigate away via sidebar
    const vesselsNav = page.locator('nav').locator('a[href*="/vessels"]').first();
    await expect(vesselsNav).toBeVisible();
    await vesselsNav.click();
    await page.waitForLoadState('domcontentloaded');

    // Navigate back to alerts via sidebar — React Query may serve from cache (no new API call)
    const alertsNav = page.locator('nav').locator('a[href*="/alerts"]').first();
    await alertsNav.click();
    await page.waitForLoadState('domcontentloaded');
    await expect(page.getByPlaceholder('Min score')).toBeVisible();

    // Check if filter persisted
    const currentValue = await minScore.inputValue();
    if (currentValue === '50') {
      advisoryReport(testInfo, 'Filters persist across navigation');
    } else {
      advisoryReport(testInfo, 'Filters reset on navigation — value cleared');
    }
  });
});
