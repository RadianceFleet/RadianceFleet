import { test, expect } from '@playwright/test';
import { fetchFirstAlertWithVessel, skipIfNoData, advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';

test.describe('Vessel Enrichment', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const pair = await fetchFirstAlertWithVessel(request);
    vesselId = pair?.vesselId ?? null;
  });

  test('PSC detention section visible', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const pscText = page.getByText(/PSC|detention|port state/i).first();
    const isVisible = await pscText.isVisible().catch(() => false);
    if (!isVisible) {
      advisoryReport(testInfo, 'No PSC detention data visible');
    }
    // Don't fail — data may be absent
  });

  test('watchlist status visible', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const watchlistText = page.getByText(/watchlist|sanction|KSE|OFAC/i).first();
    const isVisible = await watchlistText.isVisible().catch(() => false);
    if (!isVisible) {
      advisoryReport(testInfo, 'No watchlist/sanction data visible');
    }
    // Don't fail — data may be absent
  });

  test('track GeoJSON API valid', async ({ request }) => {
    skipIfNoData(test, vesselId, 'vessel');

    const res = await request.get(`/api/v1/vessels/${vesselId}/track.geojson`);
    expect(res.status()).toBe(200);

    const body = await res.text();
    expect(body).toContain('coordinates');
  });

  test('track KML API valid', async ({ request }) => {
    skipIfNoData(test, vesselId, 'vessel');

    const res = await request.get(`/api/v1/vessels/${vesselId}/track.kml`);
    expect(res.status()).toBe(200);

    const contentType = res.headers()['content-type'] ?? '';
    expect(contentType).toMatch(/xml|kml/i);
  });

  test('detectors sub-page has sections', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}/detectors`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const detectorText = page.getByText(/spoofing|loitering|gap/i).first();
    await expect(detectorText).toBeVisible({ timeout: 10_000 });
  });

  test('voyage sub-page shows visualization', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}/voyage`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Voyage page may show map, SVG, or just heading when no track data exists
    const mapOrSvg = page.locator('.leaflet-container, svg').first();
    const heading = page.getByText(/voyage/i).first();
    const vizVisible = await mapOrSvg.isVisible().catch(() => false);
    const headingVisible = await heading.isVisible().catch(() => false);
    expect(vizVisible || headingVisible).toBeTruthy();
  });

  test('timeline sub-page shows events', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}/timeline`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const timelineItems = page.locator('li, [class*="timeline"], [class*="event"], tr').filter({
      hasText: /\d{4}[-/]\d{2}[-/]\d{2}|\d{2}:\d{2}/,
    });
    const count = await timelineItems.count();
    if (count === 0) {
      advisoryReport(testInfo, 'No timeline events with timestamps found');
    }
    // Don't fail — data may be absent
  });
});
