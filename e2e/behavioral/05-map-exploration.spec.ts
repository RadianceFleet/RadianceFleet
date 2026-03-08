import { test, expect } from '@playwright/test';
import { waitForAlertMap } from './helpers/api-monitor';
import { advisoryReport, skipIfEmpty } from './helpers/data-guard';
import { MapOverviewPage } from './page-objects/MapOverviewPage';

test.describe('Map Exploration', () => {
  test('map loads with tiles', async ({ page }) => {
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    await expect(map.mapContainer).toBeVisible();
    await expect(map.tileImages).toHaveCount(
      await map.tileImages.count().then((c) => (c >= 4 ? c : 4)),
      { timeout: 10_000 },
    );
    const tileCount = await map.tileImages.count();
    expect(tileCount).toBeGreaterThanOrEqual(4);
  });

  test('5 overlay checkboxes exist', async ({ page }) => {
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    for (const label of MapOverviewPage.LAYER_LABELS) {
      await expect(map.layerCheckbox(label)).toBeVisible();
    }
  });

  test('layer toggles work', async ({ page }) => {
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    // Toggle Corridors off
    await map.layerCheckbox('Corridors').click();
    // Toggle Loitering Zones on
    await map.layerCheckbox('Loitering Zones').click();

    await map.assertNoErrors();
  });

  test('zoom controls work', async ({ page }) => {
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    await map.zoomIn.click();
    await page.waitForTimeout(300);
    await map.zoomOut.click();
    await page.waitForTimeout(300);

    await expect(map.mapContainer).toBeVisible();
    await map.assertNoErrors();
  });

  test('markers report count', async ({ page }, testInfo) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    const count = await map.markers.count();
    advisoryReport(testInfo, `Map has ${count} markers`);
  });

  test('marker click opens popup', async ({ page }) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    const count = await map.markers.count();
    skipIfEmpty(test, count, 'map markers');

    // Markers may be outside viewport — use JS dispatch to trigger Leaflet's click handler
    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 5_000 });

    const popupText = await map.popup.textContent();
    expect(popupText).toMatch(/Alert|Score/i);
  });

  test('popup link navigates to alert detail', async ({ page }, testInfo) => {
    const mapP = waitForAlertMap(page);
    await page.goto('/map', { waitUntil: 'domcontentloaded' });
    await mapP;

    const map = new MapOverviewPage(page);
    await map.waitForContentLoad();

    const count = await map.markers.count();
    skipIfEmpty(test, count, 'map markers');

    await map.markers.first().dispatchEvent('click');
    await expect(map.popup).toBeVisible({ timeout: 5_000 });

    const link = map.popup.locator('a').first();
    const linkVisible = await link.isVisible().catch(() => false);

    if (!linkVisible) {
      advisoryReport(testInfo, 'No detail link found in popup');
      return;
    }

    await link.click();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/alerts\//);
  });
});
