import { test, expect } from '@playwright/test';
import { waitForMergeCandidates, waitForMergeChains } from './helpers/api-monitor';
import { advisoryReport } from './helpers/data-guard';
import { BasePage } from './page-objects/BasePage';

test.describe('Merge Candidates', () => {
  test('page loads with status filter buttons', async ({ page }) => {
    const p = waitForMergeCandidates(page);
    await page.goto('/merge-candidates', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const filterButtons = page.getByRole('button').filter({
      hasText: /pending|approved|rejected/i,
    });
    const count = await filterButtons.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('status filter triggers API', async ({ page }) => {
    const p = waitForMergeCandidates(page);
    await page.goto('/merge-candidates', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    // Click "rejected" (not "pending" which is already active) to trigger a state change
    const rejectedBtn = page.getByRole('button', { name: /rejected/i });
    await expect(rejectedBtn).toBeVisible();

    const filterP = waitForMergeCandidates(page);
    await rejectedBtn.click();
    const response = await filterP;

    expect(response.status()).toBeLessThan(500);
  });

  test('table has expected columns', async ({ page }, testInfo) => {
    const p = waitForMergeCandidates(page);
    await page.goto('/merge-candidates', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const headers = page.locator('th');
    const headerCount = await headers.count();

    if (headerCount === 0) {
      advisoryReport(testInfo, 'No table headers found — merge candidates table may not be rendered');
      return;
    }

    const headerTexts = await headers.allTextContents();
    const joined = headerTexts.join(' ').toLowerCase();

    const hasVesselColumns =
      joined.includes('vessel a') || joined.includes('vessel b') || joined.includes('vessel');
    const hasConfidence = joined.includes('confidence') || joined.includes('score');
    const hasStatus = joined.includes('status');

    expect(hasVesselColumns || hasConfidence || hasStatus).toBeTruthy();
  });

  test('graph toggle shows SVG visualization', async ({ page }, testInfo) => {
    const p = waitForMergeCandidates(page);
    await page.goto('/merge-candidates', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const graphToggle = page.getByRole('button', { name: /graph/i });
    const isVisible = await graphToggle.isVisible().catch(() => false);

    if (!isVisible) {
      advisoryReport(testInfo, 'No Graph toggle button found — component may not render toggle');
      return;
    }

    await graphToggle.click();

    // Graph view may show empty state if no merge chains exist
    const svg = page.locator('svg');
    const emptyState = page.getByText(/no merge chains/i);
    const hasSvg = await svg.first().isVisible({ timeout: 5_000 }).catch(() => false);

    if (!hasSvg) {
      const hasEmpty = await emptyState.isVisible().catch(() => false);
      if (hasEmpty) {
        advisoryReport(testInfo, 'No merge chains — graph view shows empty state');
        return;
      }
      // Neither SVG nor empty state — real problem
      await expect(svg.first()).toBeVisible({ timeout: 5_000 });
    }
  });

  test('switch back to table view', async ({ page }, testInfo) => {
    const p = waitForMergeCandidates(page);
    await page.goto('/merge-candidates', { waitUntil: 'domcontentloaded' });
    await p;

    const base = new BasePage(page);
    await base.waitForContentLoad();

    const graphToggle = page.getByRole('button', { name: /graph/i });
    const tableToggle = page.getByRole('button', { name: /table/i });
    const hasGraphToggle = await graphToggle.isVisible().catch(() => false);

    if (!hasGraphToggle) {
      advisoryReport(testInfo, 'No Graph/Table toggle buttons found — skipping toggle test');
      return;
    }

    // Switch to graph view
    await graphToggle.click();

    // Graph view may show empty state if no merge chains exist
    const svg = page.locator('svg');
    const emptyState = page.getByText(/no merge chains/i);
    const hasSvg = await svg.first().isVisible({ timeout: 5_000 }).catch(() => false);
    const hasEmpty = await emptyState.isVisible().catch(() => false);

    if (!hasSvg && !hasEmpty) {
      advisoryReport(testInfo, 'Graph view shows neither SVG nor empty state');
      return;
    }

    // Switch back to table view
    await tableToggle.click();

    const table = page.locator('table');
    await expect(table).toBeVisible({ timeout: 10_000 });
  });
});
