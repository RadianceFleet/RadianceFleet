import { test, expect } from '@playwright/test';
import { advisoryReport } from '../helpers/data-guard';
import { waitForStsEvents } from '../helpers/api-monitor';
import { BasePage } from '../page-objects/BasePage';

test.describe('STS Transfer Tracking', () => {
  test('page loads with STS content', async ({ page }) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const heading = page.locator('h1, h2, h3, [role="heading"]').filter({
      hasText: /sts|ship-to-ship/i,
    });
    await expect(heading.first()).toBeVisible({ timeout: 10_000 });
  });

  test('events tab shows event list', async ({ page }, testInfo) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const eventsTab = page
      .getByRole('button', { name: /events/i })
      .or(page.getByRole('tab', { name: /events/i }));

    if ((await eventsTab.count()) > 0) {
      await eventsTab.first().click();
      await page.waitForTimeout(500);
    }

    const hasTable = (await page.locator('table:visible').count()) > 0;
    const hasList = (await page.locator('[role="list"]:visible, ul:visible, ol:visible').count()) > 0;
    const hasEmpty = await page.getByText(/no .*(events|data|results)/i).isVisible().catch(() => false);

    if (!hasTable && !hasList && !hasEmpty) {
      advisoryReport(testInfo, 'Events tab showed neither table/list nor empty state');
    }
    expect(hasTable || hasList || hasEmpty).toBeTruthy();
  });

  test('chains tab accessible', async ({ page }) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const chainsTab = page
      .getByRole('button', { name: /chains/i })
      .or(page.getByRole('tab', { name: /chains/i }));

    if ((await chainsTab.count()) > 0) {
      await chainsTab.first().click();
      await page.waitForTimeout(500);
    }

    await base.assertNoErrors();
  });

  test('event entries show two vessels', async ({ page }, testInfo) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const eventsTab = page
      .getByRole('button', { name: /events/i })
      .or(page.getByRole('tab', { name: /events/i }));

    if ((await eventsTab.count()) > 0) {
      await eventsTab.first().click();
      await page.waitForTimeout(500);
    }

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No STS event rows available to verify vessel references');
      return;
    }

    // Check first row for at least 2 vessel references (links or MMSI/IMO text)
    const firstRow = rows.first();
    const vesselLinks = firstRow.locator('a[href*="/vessels/"]');
    const linkCount = await vesselLinks.count();
    const rowText = (await firstRow.textContent()) ?? '';
    const mmsiMatches = rowText.match(/\b\d{9}\b/g) ?? [];

    expect(linkCount + mmsiMatches.length).toBeGreaterThanOrEqual(2);
  });

  test('event entries show proximity metadata', async ({ page }, testInfo) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const eventsTab = page
      .getByRole('button', { name: /events/i })
      .or(page.getByRole('tab', { name: /events/i }));

    if ((await eventsTab.count()) > 0) {
      await eventsTab.first().click();
      await page.waitForTimeout(500);
    }

    const rows = page.locator('table:visible tbody tr');
    const rowCount = await rows.count();

    if (rowCount === 0) {
      advisoryReport(testInfo, 'No STS event rows available to verify proximity metadata');
      return;
    }

    const tableText = await page.locator('table:visible').textContent();
    const hasProximity = /distance|proximity|meters|nm/i.test(tableText ?? '');
    expect(hasProximity).toBeTruthy();
  });

  test('vessel links navigate', async ({ page }, testInfo) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const vesselLinks = page.locator('a[href*="/vessels/"]');
    const linkCount = await vesselLinks.count();

    if (linkCount === 0) {
      advisoryReport(testInfo, 'No vessel links found on STS events page');
      return;
    }

    await vesselLinks.first().click();
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toMatch(/\/vessels\/\d+/);
  });

  test('tab switching stable', async ({ page }) => {
    await page.goto('/sts-events', { waitUntil: 'domcontentloaded' });
    await waitForStsEvents(page).catch(() => {});
    const base = new BasePage(page);
    await base.waitForContentLoad();

    const eventsTab = page
      .getByRole('button', { name: /events/i })
      .or(page.getByRole('tab', { name: /events/i }));
    const chainsTab = page
      .getByRole('button', { name: /chains/i })
      .or(page.getByRole('tab', { name: /chains/i }));

    if ((await eventsTab.count()) > 0) {
      await eventsTab.first().click();
      await page.waitForTimeout(300);
    }

    if ((await chainsTab.count()) > 0) {
      await chainsTab.first().click();
      await page.waitForTimeout(300);
    }

    if ((await eventsTab.count()) > 0) {
      await eventsTab.first().click();
      await page.waitForTimeout(300);
    }

    await base.assertNoErrors();
    expect(page.url()).toContain('/sts-events');
  });
});
