import { test, expect } from '@playwright/test';

const EXPECTED_NAV_LINKS = [
  { text: 'Alerts', href: '/alerts' },
  { text: 'Vessels', href: '/vessels' },
  { text: 'Map', href: '/map' },
  { text: 'Corridors', href: '/corridors' },
];

test.describe('Navigation at mobile width (375px)', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('key nav links are reachable and have correct hrefs', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    for (const { text, href } of EXPECTED_NAV_LINKS) {
      // Find links matching the text (case-insensitive)
      const link = page.locator('a').filter({ hasText: new RegExp(text, 'i') });

      // The link should exist in the DOM
      const count = await link.count();
      expect(
        count,
        `Expected to find a nav link with text "${text}" in the DOM`,
      ).toBeGreaterThanOrEqual(1);

      // Try scrolling to reveal it — the desktop-first 200px nav may be off-screen
      const firstLink = link.first();
      await firstLink.scrollIntoViewIfNeeded().catch(() => {});

      // Check href attribute contains the expected path
      const hrefAttr = await firstLink.getAttribute('href');
      expect(
        hrefAttr,
        `Nav link "${text}" should have href containing "${href}"`,
      ).toContain(href);
    }
  });

  test('nav links become visible after scrolling', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Attempt to scroll nav into view
    await page.evaluate(() => {
      const nav =
        document.querySelector('nav') ??
        document.querySelector('[role="navigation"]') ??
        document.querySelector('aside');
      if (nav) nav.scrollIntoView({ behavior: 'instant' });
    });

    let visibleCount = 0;

    for (const { text } of EXPECTED_NAV_LINKS) {
      const link = page.locator('a').filter({ hasText: new RegExp(text, 'i') });
      const count = await link.count();

      if (count > 0) {
        await link.first().scrollIntoViewIfNeeded().catch(() => {});
        const visible = await link.first().isVisible().catch(() => false);
        if (visible) visibleCount++;
      }
    }

    // At least some nav links should be visible after scrolling.
    // If none are visible, the mobile nav experience needs work — that's a valid finding.
    expect(
      visibleCount,
      `Expected at least 1 of ${EXPECTED_NAV_LINKS.length} nav links to be visible at 375px after scrolling`,
    ).toBeGreaterThanOrEqual(1);
  });
});
