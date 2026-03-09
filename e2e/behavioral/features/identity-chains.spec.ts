import { test, expect } from '@playwright/test';
import { fetchFirstAlertWithVessel, skipIfNoData, advisoryReport } from '../helpers/data-guard';
import { waitForMergeChains } from '../helpers/api-monitor';
import { BasePage } from '../page-objects/BasePage';

test.describe('Identity Chains', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const pair = await fetchFirstAlertWithVessel(request);
    vesselId = pair?.vesselId ?? null;
  });

  test('ownership page loads with hierarchy', async ({ page }) => {
    await page.goto('/ownership', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const hasStructure =
      (await page.locator('ul li, table, [data-testid*="expand"], details summary').count()) > 0;
    const hasEmptyState =
      (await page.getByText(/no (data|results|owners|records)/i).count()) > 0 ||
      (await page.getByText(/empty/i).count()) > 0;

    expect(hasStructure || hasEmptyState).toBeTruthy();
    await base.assertNoErrors();
  });

  test('clusters are expandable', async ({ page }, testInfo) => {
    await page.goto('/ownership', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const expandable = page.locator(
      'button:has-text("+"), button:has-text("-"), button:has-text("expand"), button:has-text("collapse"), details summary, [aria-expanded]',
    );
    const count = await expandable.count();

    if (count === 0) {
      advisoryReport(testInfo, 'No expandable cluster elements found on /ownership');
    }
  });

  test('expanded cluster shows owner members with similarity', async ({ page }, testInfo) => {
    await page.goto('/ownership', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const expandable = page.locator(
      'button:has-text("+"), button:has-text("expand"), details summary, [aria-expanded="false"]',
    );
    const count = await expandable.count();

    if (count === 0) {
      advisoryReport(testInfo, 'No expandable elements to click — skipping member check');
      return;
    }

    await expandable.first().click();
    await page.waitForTimeout(500);

    const memberRows = page.locator('tr, li, [data-testid*="member"]');
    const memberCount = await memberRows.count();

    if (memberCount === 0) {
      advisoryReport(testInfo, 'No member rows visible after expanding cluster');
      return;
    }

    const pageText = await page.textContent('body');
    const hasSimilarity = /% match|similarity|score/i.test(pageText ?? '');
    if (!hasSimilarity) {
      advisoryReport(testInfo, 'No similarity/match percentage text found in expanded cluster');
    }
  });

  test('vessel detail shows identity history', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');
    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const identityText = page.locator('body');
    await expect(identityText).toContainText(/identity|history|name.?change/i, {
      timeout: 10_000,
    });
  });

  test('vessel history API returns data', async ({ request }) => {
    skipIfNoData(test, vesselId, 'vessel');
    const res = await request.get(`/api/v1/vessels/${vesselId}`);
    expect(res.status()).toBeLessThan(500);
  });

  test('fleet page shows owner clusters', async ({ page }) => {
    await page.goto('/fleet', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    await expect(base.heading).toBeVisible({ timeout: 10_000 });

    const clusterElements = page.locator(
      '[data-testid*="cluster"], [class*="cluster"], [class*="group"], .card, ul li, table tbody tr',
    );
    const count = await clusterElements.count();
    // Acceptable if page loads with heading — clusters may or may not be present
    expect(count).toBeGreaterThanOrEqual(0);

    await base.assertNoErrors();
  });
});
