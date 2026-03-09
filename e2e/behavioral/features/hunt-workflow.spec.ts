import { test, expect } from '@playwright/test';
import { advisoryReport } from '../helpers/data-guard';
import { BasePage } from '../page-objects/BasePage';
import { HuntPage } from '../page-objects/HuntPage';

test.describe('Hunt Workflow', () => {
  test('hunt page has two tabs', async ({ page }) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await expect(hp.missionsTab).toBeVisible({ timeout: 10_000 });
    await expect(hp.targetsTab).toBeVisible({ timeout: 10_000 });
  });

  test('targets tab shows list or empty state', async ({ page }, testInfo) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.targetsTab.click();
    await page.waitForTimeout(500);

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();
    const hasEmpty = await page.getByText(/no.*target|no.*results/i).isVisible().catch(() => false);

    if (rowCount === 0 && !hasEmpty) {
      advisoryReport(testInfo, 'Targets tab showed neither rows nor empty state');
    }

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No target data available');
    }

    expect(rowCount > 0 || hasEmpty).toBeTruthy();
  });

  test('missions tab shows list or empty state', async ({ page }, testInfo) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.missionsTab.click();
    await page.waitForTimeout(500);

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();
    const hasEmpty = await page.getByText(/no.*mission|no.*results/i).isVisible().catch(() => false);

    if (rowCount === 0 && !hasEmpty) {
      advisoryReport(testInfo, 'Missions tab showed neither rows nor empty state');
    }

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No mission data available');
    }

    expect(rowCount > 0 || hasEmpty).toBeTruthy();
  });

  test('target entries show vessel profile columns', async ({ page }, testInfo) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.targetsTab.click();
    await page.waitForTimeout(500);

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No targets available to verify column headers');
      return;
    }

    const headers = page.locator('table:visible th');
    const headerText = await headers.allTextContents();
    const joined = headerText.join(' ').toLowerCase();

    const hasVesselId = /vessel|mmsi|imo/i.test(joined);
    const hasDwt = /dwt/i.test(joined);
    const hasLoa = /loa|length/i.test(joined);
    const hasLat = /lat/i.test(joined);
    const hasLon = /lon/i.test(joined);

    if (!hasVesselId) advisoryReport(testInfo, 'No vessel ID column found in target headers');
    if (!hasDwt) advisoryReport(testInfo, 'No DWT column found in target headers');
    if (!hasLoa) advisoryReport(testInfo, 'No LOA column found in target headers');
    if (!hasLat) advisoryReport(testInfo, 'No latitude column found in target headers');
    if (!hasLon) advisoryReport(testInfo, 'No longitude column found in target headers');

    expect(hasVesselId || hasDwt || hasLoa || hasLat || hasLon).toBeTruthy();
  });

  test('mission entries show status', async ({ page }, testInfo) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.missionsTab.click();
    await page.waitForTimeout(500);

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No mission data available to verify status');
      return;
    }

    const tableText = await page.locator('table:visible').textContent();
    const hasStatus = /status|active|completed|pending/i.test(tableText ?? '');
    expect(hasStatus).toBeTruthy();
  });

  test('create target button visible', async ({ page }) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.targetsTab.click();
    await page.waitForTimeout(500);

    await expect(hp.createTargetBtn).toBeVisible({ timeout: 10_000 });
  });

  test('create mission button visible', async ({ page }) => {
    await page.goto('/hunt', { waitUntil: 'domcontentloaded' });
    const hp = new HuntPage(page);
    await hp.waitForContentLoad();

    await hp.missionsTab.click();
    await page.waitForTimeout(500);

    await expect(hp.createMissionBtn).toBeVisible({ timeout: 10_000 });
  });
});
