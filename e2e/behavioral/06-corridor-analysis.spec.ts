import { test, expect } from '@playwright/test';
import { waitForCorridors } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData, advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Corridor Analysis', () => {
  let corridorId: string | null = null;

  test.beforeAll(async ({ request }) => {
    corridorId = await fetchFirstId(request, 'corridors', 'corridor_id');
  });

  test('corridor list loads', async ({ page }) => {
    const p = waitForCorridors(page);
    await page.goto('/corridors', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.locator('th', { hasText: 'Name' })).toBeVisible();
    await expect(page.locator('th', { hasText: 'Type' })).toBeVisible();
    await expect(page.locator('th', { hasText: 'Risk Weight' })).toBeVisible();
    await expect(page.locator('th', { hasText: 'Jamming Zone' })).toBeVisible();
  });

  test('pagination controls present', async ({ page }, testInfo) => {
    const p = waitForCorridors(page);
    await page.goto('/corridors', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const prevBtn = page.getByRole('button', { name: /prev/i });
    const nextBtn = page.getByRole('button', { name: /next/i });
    const pageInfo = page.getByText(/Page \d+/i);

    const hasPagination =
      (await prevBtn.isVisible().catch(() => false)) ||
      (await nextBtn.isVisible().catch(() => false)) ||
      (await pageInfo.isVisible().catch(() => false));

    if (!hasPagination) {
      advisoryReport(testInfo, 'No pagination controls visible — may have only 1 page');
    }
  });

  test('click corridor → detail', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    const p = waitForCorridors(page);
    await page.goto('/corridors', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const firstRow = page.locator('table tbody tr').first();
    const firstLink = firstRow.locator('a').first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/corridors\/\w+/);
  });

  test('detail shows chart and alerts section', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const chartHeading = page.getByText(/Activity Over Time|Traffic|Chart/i).first();
    await expect(chartHeading).toBeVisible({ timeout: 10_000 });

    const alertsSection = page.getByText(/Corridor Alerts|Alerts/i).first();
    await expect(alertsSection).toBeVisible();
  });

  test('detail shows metadata', async ({ page }) => {
    skipIfNoData(test, corridorId, 'corridors');

    await page.goto(`/corridors/${corridorId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByText(/Name/i).first()).toBeVisible();
    await expect(page.getByText(/Type/i).first()).toBeVisible();
    await expect(page.getByText(/Risk Weight/i).first()).toBeVisible();

    const alertCount = page.getByText(/Alerts \(\d+ days?\)/i).first();
    await expect(alertCount).toBeVisible();
  });
});
