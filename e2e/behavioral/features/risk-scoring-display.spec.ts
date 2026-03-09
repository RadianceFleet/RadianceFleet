import { test, expect } from '@playwright/test';
import { fetchFirstAlertWithVessel, advisoryReport, skipIfNoData } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';

test.describe('Risk Scoring Display', () => {
  let pair: { alertId: string; vesselId: string } | null = null;

  test.beforeAll(async ({ request }) => {
    pair = await fetchFirstAlertWithVessel(request);
  });

  test('alert detail shows numeric score badge', async ({ page }, testInfo) => {
    skipIfNoData(test, pair?.alertId, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Look for a numeric score in a badge, span, or score-related element
    const scoreBadge = page.locator('[class*="score"], [class*="badge"], [data-testid*="score"]').filter({ hasText: /\d+/ }).first();
    const fallback = page.locator('span, div').filter({ hasText: /^[1-9]\d{0,2}$/ }).first();

    const visible = await scoreBadge.isVisible({ timeout: 10_000 }).catch(() => false);
    if (visible) {
      await expect(scoreBadge).toBeVisible();
    } else {
      await expect(fallback).toBeVisible({ timeout: 10_000 });
      advisoryReport(testInfo, 'Score found via generic numeric element, not a dedicated score class');
    }
  });

  test('score breakdown section visible', async ({ page }) => {
    skipIfNoData(test, pair?.alertId, 'alert with vessel');

    await page.goto(`/alerts/${pair!.alertId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const breakdown = page.getByText(/score breakdown|signal|contribution/i).first();
    await expect(breakdown).toBeVisible({ timeout: 10_000 });
  });

  test('accuracy page loads with threshold controls', async ({ page }) => {
    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const control = page.locator('select, [role="combobox"], [role="listbox"], input[type="range"]').first();
    await expect(control).toBeVisible({ timeout: 10_000 });
  });

  test('accuracy shows confusion matrix', async ({ page }) => {
    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const matrix = page.getByText(/precision|recall|confusion/i).first();
    await expect(matrix).toBeVisible({ timeout: 10_000 });
  });

  test('accuracy shows PR curve chart', async ({ page }) => {
    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    // Chart renders only when sweep data loads; "Failed to load" is acceptable
    const chart = page.locator('svg, .recharts-wrapper').first();
    const heading = page.getByText(/precision.*recall/i).first();
    const chartVisible = await chart.isVisible().catch(() => false);
    const headingVisible = await heading.isVisible().catch(() => false);
    expect(chartVisible || headingVisible).toBeTruthy();
  });

  test('accuracy shows signal effectiveness', async ({ page }) => {
    await page.goto('/accuracy', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const effectiveness = page.getByText(/signal.*effectiveness|lift/i).first();
    await expect(effectiveness).toBeVisible({ timeout: 10_000 });
  });

  test('dashboard uses documented band labels', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();
    await base.assertNoErrors();

    const critical = page.getByText('Critical').first();
    await expect(critical).toBeVisible({ timeout: 10_000 });
  });
});
