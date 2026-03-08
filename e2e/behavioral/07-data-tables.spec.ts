import { test, expect } from '@playwright/test';
import { waitForAlerts, waitForCorridors } from './helpers/api-monitor';
import { advisoryReport } from './helpers/data-guard';
import { AlertListPage } from './page-objects/AlertListPage';
import { BasePage } from './page-objects/BasePage';

test.describe('Data Tables', () => {
  test('alert table sort indicator toggles', async ({ page }) => {
    const p = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await p;

    const alp = new AlertListPage(page);
    const scoreHeader = alp.sortHeader('Score');
    await expect(scoreHeader).toBeVisible();

    const initialText = await scoreHeader.textContent();
    expect(initialText).toContain('▼');

    await scoreHeader.click();
    // Sort may be client-side (React Query cache) — wait for header text to update
    await expect(scoreHeader).toContainText('▲', { timeout: 5_000 });

    await scoreHeader.click();
    await expect(scoreHeader).toContainText('▼', { timeout: 5_000 });
  });

  test('alert table row structure', async ({ page }, testInfo) => {
    const p = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const firstRow = page.locator('table tbody tr').first();
    const isVisible = await firstRow.isVisible().catch(() => false);

    if (!isVisible) {
      advisoryReport(testInfo, 'No alert rows found — table may be empty');
      return;
    }

    const cells = firstRow.locator('td');
    const cellCount = await cells.count();
    expect(cellCount).toBeGreaterThanOrEqual(3);

    // Alert link with #N pattern
    const alertLink = firstRow.locator('a[href*="/alerts/"]');
    await expect(alertLink).toBeVisible();
    const linkText = await alertLink.textContent();
    expect(linkText).toMatch(/#\d+/);

    // Vessel name link
    const vesselLink = firstRow.locator('a[href*="/vessels/"]');
    await expect(vesselLink).toBeVisible();

    // Score value — a cell containing a number
    const rowText = await firstRow.textContent();
    expect(rowText).toMatch(/\d+/);
  });

  test('Prev disabled on page 1', async ({ page }) => {
    const p = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await p;

    const alp = new AlertListPage(page);
    await expect(alp.prevButton).toBeVisible();
    await expect(alp.prevButton).toBeDisabled();
  });

  test('Next disabled on last page', async ({ page }, testInfo) => {
    const p = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await p;

    const alp = new AlertListPage(page);
    const pageInfoLocator = alp.pageInfo();
    const isVisible = await pageInfoLocator.isVisible().catch(() => false);

    if (!isVisible) {
      advisoryReport(testInfo, 'No pagination info visible — cannot determine page count');
      return;
    }

    const text = await pageInfoLocator.textContent();
    const match = text?.match(/Page \d+ of (\d+)/);
    const totalPages = match ? parseInt(match[1], 10) : 1;

    if (totalPages <= 1) {
      await expect(alp.nextButton).toBeDisabled();
      return;
    }

    // Navigate to last page (max 5 clicks to avoid runaway)
    for (let i = 1; i < totalPages && i <= 5; i++) {
      const nextP = waitForAlerts(page);
      await alp.nextButton.click();
      await nextP;
    }

    await expect(alp.nextButton).toBeDisabled();
  });

  test('corridor table has expected columns', async ({ page }) => {
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
});
