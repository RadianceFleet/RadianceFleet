import { test, expect } from '@playwright/test';
import { waitForAlerts } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData, advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

const NAV_LABELS = [
  'Dashboard',
  'Alerts',
  'Vessels',
  'Map',
  'STS Events',
  'Dark Vessels',
  'Detections',
  'Corridors',
  'Watchlist',
  'Fleet',
  'Ownership',
  'Merges',
  'Hunt',
  'Ingest',
  'Accuracy',
  'Detect',
  'Tips',
  'Support',
] as const;

test.describe('Navigation', () => {
  test('sidebar shows all 18 nav links', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const nav = base.sidebar;
    for (const label of NAV_LABELS) {
      await expect(nav.getByText(label, { exact: true })).toBeVisible();
    }

    const links = nav.locator('a');
    await expect(links).toHaveCount(18);
  });

  test('clicking nav links navigates', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const nav = base.sidebar;

    // Alerts
    const alertsP = waitForAlerts(page);
    await nav.getByText('Alerts', { exact: true }).click();
    await alertsP;
    expect(page.url()).toContain('/alerts');
    await expect(base.heading).toBeVisible();

    // Vessels
    await nav.getByText('Vessels', { exact: true }).click();
    await base.waitForContentLoad();
    expect(page.url()).toContain('/vessels');
    await expect(base.heading).toBeVisible();

    // Map
    await nav.getByText('Map', { exact: true }).click();
    await base.waitForContentLoad();
    expect(page.url()).toContain('/map');
    // Map page has no heading — it's a full-screen map
    await expect(page.locator('.leaflet-container')).toBeVisible({ timeout: 10_000 });

    // Corridors
    await nav.getByText('Corridors', { exact: true }).click();
    await base.waitForContentLoad();
    expect(page.url()).toContain('/corridors');
    await expect(base.heading).toBeVisible();
  });

  test('active link has accent styling', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alertsLink = page.locator('nav a', { hasText: 'Alerts' });
    await expect(alertsLink).toBeVisible();

    const borderLeft = await alertsLink.evaluate(
      (el) => getComputedStyle(el).borderLeftStyle,
    );
    expect(borderLeft).toBe('solid');

    const borderWidth = await alertsLink.evaluate(
      (el) => getComputedStyle(el).borderLeftWidth,
    );
    expect(borderWidth).toBe('3px');
  });

  test('deep links work via URL', async ({ page }) => {
    const base = new BasePage(page);

    await page.goto('/corridors', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await expect(base.heading).toBeVisible();
    await base.assertNoErrors();

    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await expect(base.heading).toBeVisible();
    await base.assertNoErrors();
  });

  test('alert list → alert detail link', async ({ page, request }) => {
    const alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
    skipIfNoData(test, alertId, 'alerts');

    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const firstLink = page.locator('table a[href*="/alerts/"]').first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);
  });

  test('alert detail → vessel link', async ({ page, request }) => {
    const alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
    skipIfNoData(test, alertId, 'alerts');

    await page.goto(`/alerts/${alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const vesselLink = page.locator('main a[href*="/vessels/"]').first();
    await expect(vesselLink).toBeVisible();
    await vesselLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/vessels\/\d+/);
  });

  test('back navigation via breadcrumb', async ({ page, request }, testInfo) => {
    const alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
    skipIfNoData(test, alertId, 'alerts');

    await page.goto(`/alerts/${alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const breadcrumb = page.locator('main a').filter({ hasText: /all alerts|back to alerts|alerts/i }).first();
    const isVisible = await breadcrumb.isVisible().catch(() => false);

    if (!isVisible) {
      advisoryReport(testInfo, 'No breadcrumb/back link found on alert detail page');
      return;
    }

    const alertsP = waitForAlerts(page);
    await breadcrumb.click();
    await alertsP;

    expect(page.url()).toMatch(/\/alerts(\?|$)/);
  });

  test('browser back button works', async ({ page, request }) => {
    const alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
    skipIfNoData(test, alertId, 'alerts');

    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const firstLink = page.locator('table a[href*="/alerts/"]').first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);

    await page.goBack();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts(\?|$)/);
  });
});
