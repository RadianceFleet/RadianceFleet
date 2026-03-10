import { test, expect } from '@playwright/test';
import { waitForAlerts } from '../helpers/api-monitor';
import { fetchFirstAlertWithVessel, skipIfNoData } from '../helpers/data-guard';
import { AlertListPage } from '../page-objects/AlertListPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('Export Verification Journey', () => {
  let pair: { alertId: string; vesselId: string } | null = null;

  test.beforeAll(async ({ request }) => {
    pair = await fetchFirstAlertWithVessel(request);
  });

  test('alert detail has export buttons', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByRole('button', { name: /Export Markdown/ })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /Export JSON/ })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /Export CSV/ })).toBeVisible({ timeout: 10_000 });
  });

  test('alert detail has Share button', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const shareButton = page.getByRole('button', { name: /Share/ });
    await expect(shareButton).toBeVisible({ timeout: 10_000 });
    await expect(shareButton).toHaveAttribute('title', 'Copy link to clipboard');
  });

  test('alert list has CSV export link', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    await expect(alp.exportCsvLink).toBeVisible({ timeout: 10_000 });
  });

  test('vessel detail has Share button', async ({ page }) => {
    skipIfNoData(test, pair?.vesselId ?? null, 'vessel');

    await page.goto(`/vessels/${pair!.vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const shareButton = page.getByRole('button', { name: /Share/ });
    await expect(shareButton).toBeVisible({ timeout: 10_000 });
  });

  test('CSV export link includes current filters', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const filteredP = waitForAlerts(page);
    await alp.minScore.fill('60');
    await filteredP;

    await expect(alp.exportCsvLink).toHaveAttribute('href', /min_score=60/, { timeout: 10_000 });
  });

  test('alert CSV column picker opens', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const exportCsvButton = page.getByRole('button', { name: /Export CSV/ });
    await expect(exportCsvButton).toBeVisible({ timeout: 10_000 });
    await exportCsvButton.click();

    await expect(page.locator('input[type="checkbox"]').first()).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole('button', { name: /Select all|Deselect all/ }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: /Download CSV/ })).toBeVisible({ timeout: 10_000 });
  });

  test('export review gate note visible', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByText(/export requires status/i)).toBeVisible({ timeout: 10_000 });
  });
});
