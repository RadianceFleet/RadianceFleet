import { test, expect } from '@playwright/test';

test.describe('Map on Pixel 7 viewport', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('map renders with tiles at mobile resolution', async ({ page }) => {
    await page.goto('/map');
    await page.waitForLoadState('domcontentloaded');

    // Leaflet container is present and visible
    const leafletContainer = page.locator('.leaflet-container');
    await expect(leafletContainer).toBeVisible({ timeout: 15_000 });

    // At least 4 tile images have loaded (check src attribute, not .leaflet-tile-loaded)
    const tiles = page.locator('.leaflet-tile-pane img[src]');
    await expect(tiles.first()).toBeVisible({ timeout: 15_000 });

    const tileCount = await tiles.count();
    expect(
      tileCount,
      'Map should render at least 4 tile images on mobile',
    ).toBeGreaterThanOrEqual(4);

    // No error boundary
    const errorBoundary = page.getByText('Something went wrong');
    await expect(errorBoundary).not.toBeVisible({ timeout: 3_000 });
  });
});
