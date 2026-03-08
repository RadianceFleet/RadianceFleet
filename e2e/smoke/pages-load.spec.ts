import { test, expect } from '@playwright/test';
import { STATIC_PAGES } from '../fixtures/routes.generated';

/** Console error messages that are benign noise and should be ignored. */
const BENIGN_CONSOLE_PATTERNS = [
  'favicon',
  'ResizeObserver loop',
  'ResizeObserver loop completed with undelivered notifications',
];

function isBenign(msg: string): boolean {
  return BENIGN_CONSOLE_PATTERNS.some((p) =>
    msg.toLowerCase().includes(p.toLowerCase()),
  );
}

for (const { path, name } of STATIC_PAGES) {
  test(`page "${name}" (${path}) loads without errors`, async ({ page }) => {
    const pageErrors: Error[] = [];
    const consoleErrors: string[] = [];

    page.on('pageerror', (err) => {
      pageErrors.push(err);
    });

    page.on('console', (msg) => {
      if (msg.type() === 'error' && !isBenign(msg.text())) {
        consoleErrors.push(msg.text());
      }
    });

    await page.goto(path);
    await page.waitForLoadState('domcontentloaded');

    // No uncaught exceptions
    expect(
      pageErrors,
      `Uncaught page errors on ${path}: ${pageErrors.map((e) => e.message).join('; ')}`,
    ).toHaveLength(0);

    // No error boundary
    const errorBoundary = page.getByText('Something went wrong');
    await expect(errorBoundary).not.toBeVisible({ timeout: 3000 });

    // Page-specific assertions
    if (path === '/map') {
      const leaflet = page.locator('.leaflet-container');
      await expect(leaflet).toBeVisible({ timeout: 15000 });

      const tiles = page.locator('.leaflet-tile-pane img[src]');
      await expect(tiles.first()).toBeVisible({ timeout: 15000 });
      const tileCount = await tiles.count();
      expect(tileCount, 'Map should render at least 4 tile images').toBeGreaterThanOrEqual(4);
    } else {
      // Generic: some heading or content should be visible
      const heading = page.locator('h1, h2, h3, [role="heading"]').first();
      const hasHeading = await heading.isVisible().catch(() => false);

      if (!hasHeading) {
        // Fallback: at least some text content in main area
        const mainContent = page.locator('main, #root, [role="main"]').first();
        await expect(mainContent).toBeVisible({ timeout: 10000 });
      }
    }

    // Log console errors as warnings but don't hard-fail on them
    if (consoleErrors.length > 0) {
      console.warn(
        `[${name}] ${consoleErrors.length} console error(s): ${consoleErrors.slice(0, 3).join('; ')}`,
      );
    }
  });
}
