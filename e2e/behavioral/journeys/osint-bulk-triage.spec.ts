import { test, expect } from '@playwright/test';
import { waitForAlerts } from '../helpers/api-monitor';
import { advisoryReport } from '../helpers/data-guard';
import { AlertListPage } from '../page-objects/AlertListPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('OSINT Bulk Triage Journey', () => {
  test('score + status filters simultaneously', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const scoreP = waitForAlerts(page);
    await alp.minScore.fill('60');
    await scoreP;

    const statusP = waitForAlerts(page);
    await alp.statusSelect.selectOption('new');
    const response = await statusP;

    expect(response.url()).toContain('min_score=60');
    expect(response.url()).toContain('status=new');
  });

  test('three filters at once', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const scoreP = waitForAlerts(page);
    await alp.minScore.fill('50');
    await scoreP;

    const statusP = waitForAlerts(page);
    await alp.statusSelect.selectOption('new');
    await statusP;

    const vesselP = waitForAlerts(page);
    await alp.vesselName.fill('a');
    const response = await vesselP;

    expect(response.url()).toContain('min_score=50');
    expect(response.url()).toContain('status=new');
    expect(response.url()).toContain('vessel_name=a');
  });

  test('clear one filter, others remain', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const scoreP = waitForAlerts(page);
    await alp.minScore.fill('60');
    await scoreP;

    const statusP = waitForAlerts(page);
    await alp.statusSelect.selectOption('new');
    await statusP;

    const clearedP = waitForAlerts(page);
    await alp.minScore.fill('');
    const response = await clearedP;

    expect(response.url()).toContain('status=new');
    expect(response.url()).not.toContain('min_score');
  });

  test('filter produces empty results gracefully', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const scoreP = waitForAlerts(page);
    await alp.minScore.fill('99');
    await scoreP;

    const vesselP = waitForAlerts(page);
    await alp.vesselName.fill('zzzzxyzzy999');
    await vesselP;

    const totalVisible = await alp.totalAlerts().isVisible().catch(() => false);
    if (totalVisible) {
      await expect(alp.totalAlerts()).toHaveText('0 alerts total');
    } else {
      const rows = page.locator('table tbody tr');
      const count = await rows.count();
      expect(count).toBe(0);
    }

    const base = new BasePage(page);
    await base.assertNoErrors();
  });

  test('pagination resets on filter change', async ({ page }, testInfo) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const nextEnabled = await alp.nextButton.isEnabled().catch(() => false);
    if (!nextEnabled) {
      advisoryReport(testInfo, 'single page of results');
      return;
    }

    const nextP = waitForAlerts(page);
    await alp.nextButton.click();
    await nextP;

    const pageInfoText = await alp.pageInfo().textContent();
    expect(pageInfoText).not.toContain('Page 1 ');

    const filterP = waitForAlerts(page);
    await alp.minScore.fill('50');
    await filterP;

    await expect(alp.pageInfo()).toHaveText(/Page 1/);
  });

  test('sort + filter combination', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    const filteredP = waitForAlerts(page);
    await alp.minScore.fill('50');
    await filteredP;

    const sortedP = waitForAlerts(page);
    await alp.sortHeader('Score').click();
    const response = await sortedP;

    expect(response.url()).toContain('min_score=50');
    expect(response.url()).toMatch(/sort/);
  });

  test('rapid filter changes (debounce)', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const alp = new AlertListPage(page);

    await alp.vesselName.fill('ab');
    await alp.vesselName.fill('abcd');

    const response = await page.waitForResponse(
      (r) => r.url().includes('/api/v1/alerts?') && r.url().includes('vessel_name=abcd'),
      { timeout: 10000 },
    );

    expect(response.url()).toContain('vessel_name=abcd');
  });
});
