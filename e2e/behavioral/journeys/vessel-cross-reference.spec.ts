import { test, expect } from '@playwright/test';
import { waitForVessels } from '../helpers/api-monitor';
import { fetchFirstId, skipIfNoData, skipIfEmpty, advisoryReport } from '../helpers/data-guard';
import { VesselSearchPage } from '../page-objects/VesselSearchPage';
import { BasePage } from '../page-objects/BasePage';

test.describe('Vessel Cross-Reference Journey', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    vesselId = await fetchFirstId(request, 'vessels', 'vessel_id');
  });

  test('vessel STS partner links visible', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const stsSection = page.getByText(/Ship-to-Ship/i).first();
    const stsVisible = await stsSection.isVisible().catch(() => false);

    if (stsVisible) {
      const partnerLinks = page.locator('a[href*="/vessels/"]');
      const count = await partnerLinks.count();
      if (count > 0) {
        await expect(partnerLinks.first()).toBeVisible({ timeout: 10_000 });
      } else {
        advisoryReport(testInfo, 'No STS events detected');
      }
    } else {
      advisoryReport(testInfo, 'No STS events detected');
    }
  });

  test('STS partner link navigates', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const partnerLinks = page.locator('a[href*="/vessels/"]');
    await partnerLinks.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const count = await partnerLinks.count();
    skipIfEmpty(test, count, 'STS partner links');

    const originalUrl = page.url();
    await partnerLinks.first().click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/vessels\/\d+/);
    expect(page.url()).not.toBe(originalUrl);
    await base.assertNoErrors();
  });

  test('vessel "View all" alerts link', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const viewAllLink = page.locator('a[href*="vessel_id="]').or(
      page.getByRole('link', { name: /View all/i }),
    ).first();
    const visible = await viewAllLink.isVisible().catch(() => false);

    if (visible) {
      const href = await viewAllLink.getAttribute('href');
      expect(href).toContain('vessel_id=');
    } else {
      advisoryReport(testInfo, '≤10 alerts, no View all link');
    }
  });

  test('vessel alert → same vessel consistency', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const alertLinks = page.locator('a[href*="/alerts/"]');
    await alertLinks.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const count = await alertLinks.count();
    skipIfEmpty(test, count, 'Recent Gap Alerts links');

    await alertLinks.first().click();
    await page.waitForLoadState('domcontentloaded');

    // Alert may not have vessel_id — vessel link is conditional
    const vesselLink = page.locator('a[href*="/vessels/"]').first();
    const vesselVisible = await vesselLink.isVisible({ timeout: 5_000 }).catch(() => false);

    if (vesselVisible) {
      const href = await vesselLink.getAttribute('href');
      expect(href).toContain(vesselId!);
    } else {
      advisoryReport(testInfo, 'Alert has no vessel link (vessel_id may be null)');
    }
  });

  test('vessel search round trip', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });

    const vsp = new VesselSearchPage(page);
    await expect(vsp.searchInput).toBeVisible({ timeout: 10_000 });

    // Vessel page doesn't fetch on load — only on search input
    const searchP = waitForVessels(page);
    await vsp.searchInput.fill('a');
    await searchP;

    const firstResult = page.locator('a[href*="/vessels/"]').first();
    await expect(firstResult).toBeVisible({ timeout: 10_000 });
    await firstResult.click();
    await page.waitForLoadState('domcontentloaded');

    // Use browser back — breadcrumb selectors can conflict with sidebar links
    await page.goBack();
    await page.waitForURL(/\/vessels/, { timeout: 10_000 });

    expect(page.url()).toMatch(/\/vessels/);
    await expect(vsp.searchInput).toBeVisible({ timeout: 10_000 });
  });

  test('loitering corridor link', async ({ page }, testInfo) => {
    skipIfNoData(test, vesselId, 'vessel');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const corridorLinks = page.locator('a[href*="/corridors/"]');
    const count = await corridorLinks.count();

    if (count > 0) {
      await corridorLinks.first().click();
      await page.waitForLoadState('domcontentloaded');

      expect(page.url()).toMatch(/\/corridors\/\d+/);
      await base.assertNoErrors();
    } else {
      advisoryReport(testInfo, 'No loitering corridor links');
    }
  });
});
