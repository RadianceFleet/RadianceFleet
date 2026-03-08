import { test, expect } from '@playwright/test';
import { waitForStats, waitForAlerts, waitForAlertTrends } from './helpers/api-monitor';
import { BasePage } from './page-objects/BasePage';

test.describe('Dashboard', () => {
  test('stat cards render with labels', async ({ page }) => {
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await statsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByText('Total Alerts')).toBeVisible();
    await expect(page.getByText(/Critical\s*\(76\+\)/)).toBeVisible();
    await expect(page.getByText('Vessels Tracked')).toBeVisible();
    await expect(page.getByText(/Multi-gap Vessels\s*\(7d\)/)).toBeVisible();
  });

  test('score distribution chart renders', async ({ page }) => {
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await statsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const section = page.getByText('Score Distribution').locator('..');
    await expect(section).toBeVisible();

    const chart = page.locator('.recharts-wrapper');
    await expect(chart.first()).toBeVisible({ timeout: 10_000 });
  });

  test('alert trend chart renders', async ({ page }) => {
    const trendsP = waitForAlertTrends(page);
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await Promise.all([trendsP, statsP]);

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const section = page.getByText('Alert Trends').locator('..');
    await expect(section).toBeVisible();

    const chart = page.locator('.recharts-wrapper');
    await expect(chart.first()).toBeVisible({ timeout: 10_000 });
  });

  test('status breakdown visible', async ({ page }) => {
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await statsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const statusLabels = [
      'new',
      'under_review',
      'documented',
      'dismissed',
      'needs_satellite_check',
    ];

    let found = 0;
    for (const label of statusLabels) {
      const el = page.getByText(label, { exact: false });
      if (await el.first().isVisible().catch(() => false)) {
        found++;
      }
    }

    expect(found).toBeGreaterThanOrEqual(1);
  });

  test('View All Alerts link navigates', async ({ page }) => {
    const statsP = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await statsP;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const link = page.getByRole('link', { name: /all alerts/i });
    await expect(link).toBeVisible();

    const alertsP = waitForAlerts(page);
    await link.click();
    await alertsP;

    expect(page.url()).toContain('/alerts');
  });
});
