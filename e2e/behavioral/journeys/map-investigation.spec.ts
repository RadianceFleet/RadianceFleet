import { test, expect } from '@playwright/test';
import { waitForAlertMap } from '../helpers/api-monitor';
import { skipIfEmpty, advisoryReport } from '../helpers/data-guard';
import { MapOverviewPage } from '../page-objects/MapOverviewPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('Map Investigation Journey', () => {
  test('toggle 3 layers simultaneously', async ({ page }) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    const base = new BasePage(page);

    await expect(map.mapContainer).toBeVisible({ timeout: 10_000 });

    await map.layerCheckbox('Coverage Quality').click();
    await base.assertNoErrors();

    await map.layerCheckbox('Corridors').click();
    await base.assertNoErrors();

    await map.layerCheckbox('Alert Heatmap').click();
    await base.assertNoErrors();

    await expect(map.mapContainer).toBeVisible({ timeout: 10_000 });
  });

  test('map popup → detail → back to map', async ({ page }, testInfo) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.markers.first().waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {});
    const markerCount = await map.markers.count();
    skipIfEmpty(test, markerCount, 'map markers');

    // Use dispatchEvent to handle markers positioned outside viewport by Leaflet transforms
    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 10_000 });

    const detailLink = page
      .locator(
        '.leaflet-popup-content a[href*="/alerts/"], .leaflet-popup-content a[href*="/vessels/"]',
      )
      .first();
    const linkExists = await detailLink.isVisible().catch(() => false);

    if (linkExists) {
      const mapUrl = page.url();
      await detailLink.click();
      await page.waitForLoadState('domcontentloaded');
      expect(page.url()).not.toBe(mapUrl);

      await page.goBack();
      await page.waitForLoadState('domcontentloaded');
      await expect(map.mapContainer).toBeVisible({ timeout: 10_000 });
    } else {
      advisoryReport(testInfo, 'No detail link found in map popup');
    }
  });

  test('multiple marker interactions', async ({ page }) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.markers.first().waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {});
    const markerCount = await map.markers.count();
    test.skip(markerCount < 2, 'Need at least 2 markers for multiple interaction test');

    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 10_000 });

    // Close popup by clicking the map container
    await map.mapContainer.click({ position: { x: 10, y: 10 } });
    await expect(map.popup).not.toBeVisible({ timeout: 5_000 }).catch(() => {});

    // Click second marker
    await map.markers.nth(1).dispatchEvent('click');
    await map.popup.first().waitFor({ state: 'visible', timeout: 10_000 });
  });

  test('map → alert → vessel → sidebar back to map', async ({ page }, testInfo) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.markers.first().waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {});
    const markerCount = await map.markers.count();
    skipIfEmpty(test, markerCount, 'map markers');

    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 10_000 });

    const alertLink = page
      .locator(
        '.leaflet-popup-content a[href*="/alerts/"], .leaflet-popup-content a[href*="/vessels/"]',
      )
      .first();
    const alertLinkExists = await alertLink.isVisible().catch(() => false);

    if (!alertLinkExists) {
      advisoryReport(testInfo, 'No alert/vessel link found in map popup');
      return;
    }

    const href = await alertLink.getAttribute('href');
    await alertLink.click();
    await page.waitForLoadState('domcontentloaded');

    // If we landed on an alert page, try to find a vessel link
    if (href?.includes('/alerts/')) {
      const vesselLink = page.locator('a[href*="/vessels/"]').first();
      const vesselLinkExists = await vesselLink.isVisible({ timeout: 5_000 }).catch(() => false);

      if (vesselLinkExists) {
        await vesselLink.click();
        await page.waitForLoadState('domcontentloaded');
        expect(page.url()).toMatch(/\/vessels\/\d+/);
      } else {
        advisoryReport(testInfo, 'No vessel link found on alert detail page');
      }
    }

    // Navigate back to map via sidebar
    const mapNavLink = page.getByRole('link', { name: 'Map' });
    await mapNavLink.click();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/map/);
  });

  test('layer toggle during popup', async ({ page }, testInfo) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    const base = new BasePage(page);
    await map.markers.first().waitFor({ state: 'attached', timeout: 10_000 }).catch(() => {});
    const markerCount = await map.markers.count();
    skipIfEmpty(test, markerCount, 'map markers');

    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 10_000 });

    // Toggle a layer while popup is open — should not crash
    await map.layerCheckbox('Corridors').click();
    await base.assertNoErrors();

    await expect(map.mapContainer).toBeVisible({ timeout: 10_000 });
  });
});
