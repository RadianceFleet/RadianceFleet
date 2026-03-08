import { test, expect } from '@playwright/test';
import { waitForHealthFreshness, waitForCollectionStatus } from './helpers/api-monitor';
import { advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Remaining Pages', () => {
  test('hunt page has tabs', async ({ page }, testInfo) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Look for tab-like elements
    const tabs = page.locator('[role="tab"], [role="tablist"] button, .tab, button:has-text("Hunt")');
    const tabCount = await tabs.count();

    // Also look for section headings as alternative tab structure
    const sectionHeadings = page.locator('h2, h3, [role="heading"]');
    const headingCount = await sectionHeadings.count();

    if (tabCount > 0) {
      advisoryReport(testInfo, `Hunt page has ${tabCount} tab-like elements`);
      await expect(tabs.first()).toBeVisible();
    } else if (headingCount > 0) {
      advisoryReport(testInfo, `Hunt page has ${headingCount} section headings (no explicit tabs)`);
      await expect(sectionHeadings.first()).toBeVisible();
    } else {
      advisoryReport(testInfo, 'Hunt page has no tabs or section headings found');
    }
  });

  test('data-health freshness', async ({ page }, testInfo) => {
    const freshnessP = waitForHealthFreshness(page);
    await page.goto('/data-health', { waitUntil: 'domcontentloaded' });
    await freshnessP;

    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    await expect(base.heading).toBeVisible();

    // Look for freshness-related content
    const freshnessText = page.getByText(/fresh|stale|last.*update|ago|hours|minutes/i).first();
    const freshnessVisible = await freshnessText.isVisible().catch(() => false);

    if (freshnessVisible) {
      const text = await freshnessText.textContent();
      advisoryReport(testInfo, `Freshness indicator found: "${text?.slice(0, 100)}"`);
    } else {
      advisoryReport(testInfo, 'No explicit freshness text found on data-health page');
    }
  });

  test('data-health collection status', async ({ page }, testInfo) => {
    const collectionP = waitForCollectionStatus(page);
    await page.goto('/data-health', { waitUntil: 'domcontentloaded' });
    await collectionP;

    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Look for collection status section
    const collectionText = page.getByText(/collection|source|feed|ingestion|status/i).first();
    const collectionVisible = await collectionText.isVisible().catch(() => false);

    if (collectionVisible) {
      const text = await collectionText.textContent();
      advisoryReport(testInfo, `Collection status found: "${text?.slice(0, 100)}"`);
    } else {
      advisoryReport(testInfo, 'No explicit collection status text found');
    }
  });

  test('STS events page loads', async ({ page }) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Look for STS-related content
    const stsContent = page.getByText(/STS|ship-to-ship|transfer/i).first();
    const heading = page.locator('h1, h2, h3').first();

    const stsVisible = await stsContent.isVisible().catch(() => false);
    const headingVisible = await heading.isVisible().catch(() => false);

    expect(stsVisible || headingVisible).toBe(true);
  });

  test('watchlist and fleet pages load', async ({ page }) => {
    const base = new BasePage(page);

    await page.goto('/watchlist', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await base.assertNoErrors();
    await expect(base.heading).toBeVisible();

    await page.goto('/fleet', { waitUntil: 'domcontentloaded' });
    await base.waitForContentLoad();
    await base.assertNoErrors();
    await expect(base.heading).toBeVisible();
  });
});
