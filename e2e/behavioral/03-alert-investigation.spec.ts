import { test, expect } from '@playwright/test';
import { waitForAlerts } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData, skipIfEmpty, advisoryReport } from './helpers/data-guard';
import { AlertListPage } from './page-objects/AlertListPage';
import { BasePage } from './page-objects/BasePage';

test.describe('Alert Investigation', () => {
  let alertId: string | null = null;

  test.beforeAll(async ({ request }) => {
    alertId = await fetchFirstId(request, 'alerts', 'gap_event_id');
  });

  test('filter bar controls visible', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    await expect(alp.minScore).toBeVisible();
    await expect(alp.statusSelect).toBeVisible();
    await expect(alp.vesselName).toBeVisible();
    await expect(alp.dateFrom).toBeVisible();
    await expect(alp.dateTo).toBeVisible();
    await expect(alp.patternsToggle).toBeVisible();
  });

  test('filter by min score', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const filteredP = waitForAlerts(page);
    await alp.minScore.fill('60');
    const response = await filteredP;

    expect(response.url()).toContain('min_score=60');
  });

  test('filter by status', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const filteredP = waitForAlerts(page);
    await alp.statusSelect.selectOption('new');
    const response = await filteredP;

    expect(response.url()).toContain('status=new');
  });

  test('filter by vessel name', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const filteredP = waitForAlerts(page);
    await alp.vesselName.fill('test');
    const response = await filteredP;

    expect(response.url()).toContain('vessel_name=test');
  });

  test('sort by Score toggles', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const scoreHeader = alp.sortHeader('Score');
    const initialText = await scoreHeader.textContent();

    const sortedP = waitForAlerts(page);
    await scoreHeader.click();
    await sortedP;

    const updatedText = await scoreHeader.textContent();
    expect(updatedText).not.toBe(initialText);
  });

  test('sort by Duration', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const sortedP = waitForAlerts(page);
    await alp.sortHeader('Duration').click();
    const response = await sortedP;

    expect(response.url()).toContain('sort_by=duration');
  });

  test('pagination Next/Prev', async ({ page }, testInfo) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const pageInfoLocator = alp.pageInfo();
    const isVisible = await pageInfoLocator.isVisible().catch(() => false);

    if (!isVisible) {
      advisoryReport(testInfo, 'No pagination info visible — may have too few alerts');
      return;
    }

    const text = await pageInfoLocator.textContent();
    const match = text?.match(/Page \d+ of (\d+)/);
    const totalPages = match ? parseInt(match[1], 10) : 1;

    if (totalPages <= 1) {
      advisoryReport(testInfo, 'Only 1 page of alerts — pagination not testable');
      return;
    }

    const nextP = waitForAlerts(page);
    await alp.nextButton.click();
    await nextP;

    await expect(page.getByText('Page 2')).toBeVisible();
    await expect(alp.prevButton).toBeEnabled();
  });

  test('Patterns Only toggle', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    const bgBefore = await alp.patternsToggle.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );

    await alp.patternsToggle.click();

    const bgAfter = await alp.patternsToggle.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    expect(bgAfter).not.toBe(bgBefore);

    await alp.patternsToggle.click();

    const bgReverted = await alp.patternsToggle.evaluate(
      (el) => getComputedStyle(el).backgroundColor,
    );
    expect(bgReverted).toBe(bgBefore);
  });

  test('click alert navigates to detail', async ({ page }) => {
    skipIfNoData(test, alertId, 'alerts');

    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);
    await alp.alertLinks.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const linkCount = await alp.alertLinks.count();
    skipIfEmpty(test, linkCount, 'alert list links');

    const firstLink = alp.alertLinks.first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/alerts\/\d+/);
  });

  test('alert detail content', async ({ page }) => {
    skipIfNoData(test, alertId, 'alerts');

    await page.goto(`/alerts/${alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const main = page.locator('main');
    const mapOrLeaflet = main.locator('.leaflet-container, [class*="map"]').first();
    await expect(mapOrLeaflet).toBeVisible({ timeout: 10_000 });

    const scoreText = main.getByText(/Risk Score|Score/i).first();
    await expect(scoreText).toBeVisible();

    const gapText = main.getByText(/Gap Details|gap/i).first();
    await expect(gapText).toBeVisible();
  });
});
