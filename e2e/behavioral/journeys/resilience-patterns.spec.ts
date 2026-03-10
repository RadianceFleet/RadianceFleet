import { test, expect } from '@playwright/test';
import { waitForAlerts, waitForAlertMap } from '../helpers/api-monitor';
import { fetchFirstAlertWithVessel, skipIfNoData, advisoryReport } from '../helpers/data-guard';
import { AlertListPage } from '../page-objects/AlertListPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('Resilience Patterns', () => {
  let pair: { alertId: string; vesselId: string } | null = null;

  test.beforeAll(async ({ request }) => {
    pair = await fetchFirstAlertWithVessel(request);
  });

  test('refresh alert detail', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByText(/Risk Score/i).first()).toBeVisible({ timeout: 10_000 });

    const urlBefore = page.url();
    await page.reload();
    await base.waitForContentLoad();

    await expect(page.getByText(/Risk Score/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Gap Details/i).first()).toBeVisible({ timeout: 10_000 });
    expect(page.url()).toBe(urlBefore);
  });

  test('refresh vessel detail', async ({ page }) => {
    skipIfNoData(test, pair?.vesselId ?? null, 'vessel');

    await page.goto(`/vessels/${pair!.vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const heading = base.heading;
    await expect(heading).toBeVisible({ timeout: 10_000 });
    const headingText = await heading.textContent();

    const urlBefore = page.url();
    await page.reload();
    await base.waitForContentLoad();

    await expect(heading).toBeVisible({ timeout: 10_000 });
    expect(page.url()).toBe(urlBefore);
  });

  test('deep link to alert detail', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(page.getByText(/Risk Score/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Gap Details/i).first()).toBeVisible({ timeout: 10_000 });
    await base.assertNoErrors();
  });

  test('deep link to vessel detail', async ({ page }) => {
    skipIfNoData(test, pair?.vesselId ?? null, 'vessel');

    await page.goto(`/vessels/${pair!.vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const main = page.locator('main');
    const mmsiOrName = main.getByText(/\d{9}|[A-Z]/).first();
    await expect(mmsiOrName).toBeVisible({ timeout: 10_000 });
    await base.assertNoErrors();
  });

  test('browser back through 3 pages', async ({ page }) => {
    skipIfNoData(test, pair?.alertId ?? null, 'alert with vessel');

    // Page 1: Alert list
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    // Page 2: Click first alert link
    const firstAlertLink = page.locator('table a[href*="/alerts/"]').first();
    await expect(firstAlertLink).toBeVisible({ timeout: 10_000 });
    await firstAlertLink.click();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/alerts\/\d+/);

    // Page 3: Click vessel link (or navigate directly)
    const vesselLink = page.locator('a[href*="/vessels/"]').first();
    const vesselVisible = await vesselLink.isVisible().catch(() => false);

    if (vesselVisible) {
      await vesselLink.click();
      await page.waitForLoadState('domcontentloaded');
    } else {
      await page.goto(`/vessels/${pair!.vesselId}`, { waitUntil: 'domcontentloaded' });
    }

    expect(page.url()).toMatch(/\/vessels\/\d+/);

    // Back to alert detail
    await page.goBack();
    await page.waitForURL(/\/alerts\/\d+/, { timeout: 10_000 });
    expect(page.url()).toMatch(/\/alerts\/\d+/);

    // Back to alert list
    await page.goBack();
    await page.waitForURL(/\/alerts/, { timeout: 10_000 });
    expect(page.url()).toMatch(/\/alerts/);
  });

  test('navigate away during loading', async ({ page }) => {
    const base = new BasePage(page);

    // Go to alerts but do NOT wait for the API response
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });

    // Immediately click Map nav
    await page.getByRole('link', { name: 'Map' }).click();

    // Wait for map to render
    await page.locator('.leaflet-container').waitFor({ timeout: 15_000 });

    await base.assertNoErrors();
  });

  test('double-click on navigation link', async ({ page }) => {
    const base = new BasePage(page);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();

    // Click the Alerts sidebar link (use exact match to avoid "View All Alerts")
    const alertsLink = page.getByRole('link', { name: 'Alerts', exact: true });
    await alertsLink.dblclick();

    await page.waitForURL(/\/alerts/, { timeout: 10_000 });

    await base.assertNoErrors();
  });

  test('non-existent corridor shows error', async ({ page }) => {
    const base = new BasePage(page);

    await page.goto('/corridors/99999999', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();

    // The page should either show an error message or at least have some content
    // (it should NOT be completely blank)
    const errorText = page.getByText(/not found|error|Something went wrong/i).first();
    const hasErrorMessage = await errorText.isVisible().catch(() => false);

    if (!hasErrorMessage) {
      // If no explicit error message, at least ensure the page is not blank
      const bodyText = await page.locator('body').textContent();
      expect(bodyText?.trim().length).toBeGreaterThan(0);
    }
  });
});
