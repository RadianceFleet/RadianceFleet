import { test, expect } from '@playwright/test';
import { waitForAlerts } from './helpers/api-monitor';
import { BasePage } from './page-objects/BasePage';

test.describe('Keyboard & Accessibility', () => {
  test('tab navigates through sidebar links', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const base = new BasePage(page);
    await base.waitForContentLoad();

    // Press Tab multiple times to move focus through interactive elements
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }

    // Check that focus landed on an interactive element
    const focusedTag = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? { tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), inNav: !!el.closest('nav') } : null;
    });

    expect(focusedTag).not.toBeNull();
    // The focused element should be interactive (a, button, input, etc.) or within nav
    const isInteractive = focusedTag!.tag === 'a' || focusedTag!.tag === 'button' || focusedTag!.tag === 'input' || focusedTag!.inNav;
    expect(isInteractive).toBe(true);
  });

  test('filter inputs accept keyboard entry', async ({ page }) => {
    const alertsP = waitForAlerts(page);
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await alertsP;

    const minScore = page.getByPlaceholder('Min score');
    await expect(minScore).toBeVisible();

    // Focus the input and type via keyboard
    await minScore.focus();
    await page.keyboard.type('60');

    const value = await minScore.inputValue();
    expect(value).toBe('60');

    // Press Tab to move focus away
    await page.keyboard.press('Tab');

    // Verify focus moved to a different element
    const focusedAfterTab = await page.evaluate(() => {
      const el = document.activeElement;
      return el ? el.getAttribute('placeholder') || el.tagName.toLowerCase() : null;
    });

    expect(focusedAfterTab).not.toBe('Min score');
  });

  test('heading structure on key pages', async ({ page }) => {
    const pages = ['/', '/alerts', '/vessels', '/corridors'];
    const base = new BasePage(page);

    for (const path of pages) {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await base.waitForContentLoad();

      const h2Elements = page.locator('h2');
      const h2Count = await h2Elements.count();

      // Each page should have at least one h2 for proper heading hierarchy
      expect(h2Count, `Expected h2 on ${path}`).toBeGreaterThanOrEqual(1);
      await expect(h2Elements.first()).toBeVisible();
    }
  });
});
