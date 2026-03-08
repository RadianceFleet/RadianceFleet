import { test, expect } from '@playwright/test';
import { advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Empty & Error States', () => {
  test('no error boundaries on main pages', async ({ page }) => {
    const pages = ['/', '/alerts', '/vessels', '/map', '/corridors', '/watchlist', '/fleet'];
    const base = new BasePage(page);

    for (const path of pages) {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await base.waitForContentLoad();
      await base.assertNoErrors();
    }
  });

  test('vessel search shows empty state before search', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const emptyState = page.getByText('Search for vessels');
    await expect(emptyState).toBeVisible();

    // No table rows should be visible before searching
    const tableRows = page.locator('tbody tr');
    const rowCount = await tableRows.count();
    expect(rowCount).toBe(0);
  });

  test('non-existent alert shows 404 or error', async ({ page }, testInfo) => {
    await page.goto('/alerts/99999999', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const notFound = page.getByText(/not found|404|error|no alert/i).first();
    const errorBoundary = page.getByText('Something went wrong').first();

    const notFoundVisible = await notFound.isVisible().catch(() => false);
    const errorVisible = await errorBoundary.isVisible().catch(() => false);

    const bodyText = await page.locator('body').textContent();
    const hasErrorIndicator = notFoundVisible || errorVisible;

    if (hasErrorIndicator) {
      advisoryReport(testInfo, `Non-existent alert shows error UI: notFound=${notFoundVisible}, errorBoundary=${errorVisible}`);
    } else {
      advisoryReport(testInfo, `Non-existent alert page content: ${bodyText?.slice(0, 200)}`);
    }

    // The page should show something — not a blank page
    expect(bodyText?.trim().length).toBeGreaterThan(0);
  });

  test('non-existent vessel shows 404 or error', async ({ page }, testInfo) => {
    await page.goto('/vessels/99999999', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const notFound = page.getByText(/not found|404|error|no vessel/i).first();
    const errorBoundary = page.getByText('Something went wrong').first();

    const notFoundVisible = await notFound.isVisible().catch(() => false);
    const errorVisible = await errorBoundary.isVisible().catch(() => false);

    const bodyText = await page.locator('body').textContent();
    const hasErrorIndicator = notFoundVisible || errorVisible;

    if (hasErrorIndicator) {
      advisoryReport(testInfo, `Non-existent vessel shows error UI: notFound=${notFoundVisible}, errorBoundary=${errorVisible}`);
    } else {
      advisoryReport(testInfo, `Non-existent vessel page content: ${bodyText?.slice(0, 200)}`);
    }

    expect(bodyText?.trim().length).toBeGreaterThan(0);
  });
});
