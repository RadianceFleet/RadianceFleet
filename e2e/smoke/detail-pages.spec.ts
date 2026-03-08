import { test, expect } from '@playwright/test';

const API = '/api/v1';

/**
 * Navigate to a detail page and assert basic render health.
 * Empty-state pages are valid — we only check for errors.
 */
async function assertDetailPageRenders(
  page: import('@playwright/test').Page,
  path: string,
) {
  const pageErrors: Error[] = [];
  page.on('pageerror', (err) => pageErrors.push(err));

  await page.goto(path);
  await page.waitForLoadState('domcontentloaded');

  // No error boundary
  const errorBoundary = page.getByText('Something went wrong');
  await expect(errorBoundary).not.toBeVisible({ timeout: 3000 });

  // Some heading or content should be visible
  const heading = page.locator('h1, h2, h3, [role="heading"]').first();
  const hasHeading = await heading.isVisible().catch(() => false);

  if (!hasHeading) {
    const mainContent = page.locator('main, #root, [role="main"]').first();
    await expect(mainContent).toBeVisible({ timeout: 10000 });
  }

  expect(
    pageErrors,
    `Uncaught errors on ${path}: ${pageErrors.map((e) => e.message).join('; ')}`,
  ).toHaveLength(0);
}

test.describe('Vessel detail pages', () => {
  let vesselId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const res = await request.get(`${API}/vessels?limit=1`);
    if (res.status() !== 200) return;
    const body = await res.json();
    const items = Array.isArray(body) ? body : body.items ?? body.results ?? [];
    if (items.length > 0) {
      vesselId = String(items[0].id);
    }
  });

  test('vessel detail page renders', async ({ page }) => {
    test.skip(!vesselId, 'No vessels in database');
    await assertDetailPageRenders(page, `/vessels/${vesselId}`);
  });

  test('vessel detectors sub-page renders', async ({ page }) => {
    test.skip(!vesselId, 'No vessels in database');
    await assertDetailPageRenders(page, `/vessels/${vesselId}/detectors`);
  });

  test('vessel voyage sub-page renders', async ({ page }) => {
    test.skip(!vesselId, 'No vessels in database');
    await assertDetailPageRenders(page, `/vessels/${vesselId}/voyage`);
  });

  test('vessel timeline sub-page renders', async ({ page }) => {
    test.skip(!vesselId, 'No vessels in database');
    await assertDetailPageRenders(page, `/vessels/${vesselId}/timeline`);
  });
});

test.describe('Alert detail page', () => {
  let alertId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const res = await request.get(`${API}/alerts?limit=1`);
    if (res.status() !== 200) return;
    const body = await res.json();
    const items = Array.isArray(body) ? body : body.items ?? body.results ?? [];
    if (items.length > 0) {
      alertId = String(items[0].id);
    }
  });

  test('alert detail page renders', async ({ page }) => {
    test.skip(!alertId, 'No alerts in database');
    await assertDetailPageRenders(page, `/alerts/${alertId}`);
  });
});

test.describe('Corridor detail page', () => {
  let corridorId: string | null = null;

  test.beforeAll(async ({ request }) => {
    const res = await request.get(`${API}/corridors?limit=1`);
    if (res.status() !== 200) return;
    const body = await res.json();
    const items = Array.isArray(body) ? body : body.items ?? body.results ?? [];
    if (items.length > 0) {
      corridorId = String(items[0].id);
    }
  });

  test('corridor detail page renders', async ({ page }) => {
    test.skip(!corridorId, 'No corridors in database');
    await assertDetailPageRenders(page, `/corridors/${corridorId}`);
  });
});
