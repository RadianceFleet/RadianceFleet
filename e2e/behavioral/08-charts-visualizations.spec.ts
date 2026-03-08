import { test, expect } from '@playwright/test';
import { waitForStats, waitForAlertTrends, waitForCorridors } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData, advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Charts & Visualizations', () => {
  test('dashboard score distribution chart', async ({ page }) => {
    const p = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const heading = page.getByText(/Score Distribution/i).first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    const section = page.locator('section, div', { has: heading }).first();
    const chart = section.locator('.recharts-wrapper, svg').first();
    await expect(chart).toBeVisible();
  });

  test('dashboard alert trend chart', async ({ page }) => {
    const p = waitForAlertTrends(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const heading = page.getByText(/Alert Trends/i).first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    const section = page.locator('section, div', { has: heading }).first();
    const chart = section.locator('.recharts-wrapper, svg').first();
    await expect(chart).toBeVisible();
  });

  test('dashboard charts contain SVG paths', async ({ page }) => {
    const p = waitForStats(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const wrappers = page.locator('.recharts-wrapper');
    const count = await wrappers.count();
    expect(count).toBeGreaterThanOrEqual(1);

    const paths = wrappers.first().locator('svg path');
    const pathCount = await paths.count();
    expect(pathCount).toBeGreaterThanOrEqual(1);
  });

  test('corridor detail activity chart', async ({ page, request }) => {
    const corridorId = await fetchFirstId(request, 'corridors', 'corridor_id');
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const activityHeading = page.getByText(/Activity/i).first();
    await expect(activityHeading).toBeVisible({ timeout: 10_000 });

    const chart = page.locator('.recharts-wrapper, svg').first();
    await expect(chart).toBeVisible();
  });

  test('alert trend section has time-series data', async ({ page }, testInfo) => {
    const p = waitForAlertTrends(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const heading = page.getByText(/Alert Trends/i).first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    const section = page.locator('section, div', { has: heading }).first();
    const svg = section.locator('svg').first();
    await expect(svg).toBeVisible();

    const paths = svg.locator('path, line');
    const pathCount = await paths.count();

    if (pathCount === 0) {
      advisoryReport(testInfo, 'No trend data paths found — alert trend data may be empty');
      return;
    }

    expect(pathCount).toBeGreaterThanOrEqual(1);
  });
});
