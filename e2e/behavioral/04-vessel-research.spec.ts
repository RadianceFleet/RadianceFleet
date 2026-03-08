import { test, expect } from '@playwright/test';
import { waitForVessels } from './helpers/api-monitor';
import { fetchFirstId, skipIfNoData } from './helpers/data-guard';
import { VesselSearchPage } from './page-objects/VesselSearchPage';
import { BasePage } from './page-objects/BasePage';

test.describe('Vessel Research', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    vesselId = await fetchFirstId(request, 'vessels', 'vessel_id');
  });

  test('initial empty state', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    await expect(vsp.emptyState).toBeVisible();
  });

  test('search triggers API', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    const searchP = waitForVessels(page);
    await vsp.searchInput.fill('a');
    const response = await searchP;

    expect(response.url()).toContain('search=a');
  });

  test('flag filter', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    // Fill search first to get results
    const searchP = waitForVessels(page);
    await vsp.searchInput.fill('a');
    await searchP;

    const flagP = waitForVessels(page);
    await vsp.flagInput.fill('PA');
    const response = await flagP;

    expect(response.url()).toContain('flag=PA');
  });

  test('advanced filters toggle', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    await vsp.advancedToggle.click();
    await expect(vsp.minDwt).toBeVisible();
    await expect(vsp.maxDwt).toBeVisible();

    await vsp.advancedToggle.click();
    await expect(vsp.minDwt).not.toBeVisible();
    await expect(vsp.maxDwt).not.toBeVisible();
  });

  test('results table columns', async ({ page }) => {
    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    const searchP = waitForVessels(page);
    await vsp.searchInput.fill('a');
    await searchP;

    const headers = page.locator('th');
    const headerTexts = await headers.allTextContents();
    const joined = headerTexts.join(' ');

    expect(joined).toMatch(/MMSI/i);
    expect(joined).toMatch(/Name/i);
    expect(joined).toMatch(/Flag/i);
    expect(joined).toMatch(/Type/i);
  });

  test('click vessel navigates to detail', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessels');

    await page.goto('/vessels', { waitUntil: 'domcontentloaded' });
    const vsp = new VesselSearchPage(page);
    await vsp.waitForContentLoad();

    const searchP = waitForVessels(page);
    await vsp.searchInput.fill('a');
    await searchP;

    const firstLink = page.locator('a[href*="/vessels/"]').first();
    await expect(firstLink).toBeVisible();
    await firstLink.click();
    await page.waitForLoadState('domcontentloaded');

    expect(page.url()).toMatch(/\/vessels\/\d+/);
  });

  test('detail sections visible', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessels');

    await page.goto(`/vessels/${vesselId}`, { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const sectionText = page.getByText(/Spoofing|Loitering|STS|Recent/i).first();
    await expect(sectionText).toBeVisible({ timeout: 10_000 });
  });

  test('sub-pages render', async ({ page }) => {
    skipIfNoData(test, vesselId, 'vessels');

    const base = new BasePage(page);
    const subPaths = ['detectors', 'voyage', 'timeline'];

    for (const sub of subPaths) {
      await page.goto(`/vessels/${vesselId}/${sub}`, { waitUntil: 'domcontentloaded' });
      await base.waitForContentLoad();
      await base.assertNoErrors();
    }
  });
});
