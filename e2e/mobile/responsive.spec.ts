import { test, expect } from '@playwright/test';

const PAGES = ['/', '/alerts', '/vessels', '/map'];

/**
 * Evaluate whether horizontal overflow exists on the current page.
 * scrollWidth > clientWidth means content spills beyond the viewport.
 */
async function assertNoHorizontalOverflow(
  page: import('@playwright/test').Page,
  label: string,
) {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    overflow.scrollWidth,
    `Horizontal overflow on ${label}: scrollWidth=${overflow.scrollWidth} > clientWidth=${overflow.clientWidth}`,
  ).toBeLessThanOrEqual(overflow.clientWidth);
}

// ---------- iPhone SE (375px) ----------

test.describe('Responsive — 375px (iPhone SE)', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  for (const path of PAGES) {
    test(`no horizontal overflow on ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState('domcontentloaded');
      await assertNoHorizontalOverflow(page, `375px ${path}`);
    });
  }

  test('nav sidebar has reachable links', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // The nav may be off-screen or scrollable at narrow widths.
    // Try to find at least one key nav link, scrolling if necessary.
    const navLink = page.locator('a, [role="link"]').filter({
      hasText: /Alerts|Vessels/i,
    });

    // Scroll the page/sidebar to attempt to reveal nav links
    await page.evaluate(() => {
      const nav =
        document.querySelector('nav') ??
        document.querySelector('[role="navigation"]') ??
        document.querySelector('aside');
      if (nav) nav.scrollIntoView({ behavior: 'instant' });
    });

    const count = await navLink.count();
    expect(
      count,
      'Expected at least one nav link (Alerts or Vessels) to be in the DOM at 375px',
    ).toBeGreaterThanOrEqual(1);

    // Verify at least one is visible (possibly after scroll)
    let anyVisible = false;
    for (let i = 0; i < count; i++) {
      const visible = await navLink.nth(i).isVisible().catch(() => false);
      if (visible) {
        anyVisible = true;
        break;
      }
    }

    // If none visible, try scrolling the first one into view
    if (!anyVisible && count > 0) {
      await navLink.first().scrollIntoViewIfNeeded().catch(() => {});
      anyVisible = await navLink.first().isVisible().catch(() => false);
    }

    expect(
      anyVisible,
      'At least one nav link should be visible or become visible after scrolling at 375px',
    ).toBe(true);
  });
});

// ---------- iPad (768px) ----------

test.describe('Responsive — 768px (iPad)', () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  for (const path of PAGES) {
    test(`no horizontal overflow on ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState('domcontentloaded');
      await assertNoHorizontalOverflow(page, `768px ${path}`);
    });
  }

  test('nav sidebar has reachable links', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    await page.evaluate(() => {
      const nav =
        document.querySelector('nav') ??
        document.querySelector('[role="navigation"]') ??
        document.querySelector('aside');
      if (nav) nav.scrollIntoView({ behavior: 'instant' });
    });

    const navLink = page.locator('a, [role="link"]').filter({
      hasText: /Alerts|Vessels/i,
    });

    const count = await navLink.count();
    expect(
      count,
      'Expected at least one nav link (Alerts or Vessels) to be in the DOM at 768px',
    ).toBeGreaterThanOrEqual(1);

    let anyVisible = false;
    for (let i = 0; i < count; i++) {
      const visible = await navLink.nth(i).isVisible().catch(() => false);
      if (visible) {
        anyVisible = true;
        break;
      }
    }

    if (!anyVisible && count > 0) {
      await navLink.first().scrollIntoViewIfNeeded().catch(() => {});
      anyVisible = await navLink.first().isVisible().catch(() => false);
    }

    expect(
      anyVisible,
      'At least one nav link should be visible or become visible after scrolling at 768px',
    ).toBe(true);
  });
});
