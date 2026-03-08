import { test, expect } from '@playwright/test';

const API = '/api/v1';

/**
 * Rate-limiting verification.
 *
 * SKIPPED by default — running 65 rapid requests against production can
 * trigger WAF rules or get the test runner's IP temporarily banned.
 * Run manually with:
 *   npx playwright test security/rate-limiting.spec.ts --grep "rate limit"
 * and remove the skip annotation first, or use RATE_LIMIT_TEST=1 env var.
 *
 * Caveat: slowapi's in-memory counter is per-worker. With multiple uvicorn
 * workers behind a load balancer, the effective limit is multiplied by the
 * number of workers, so the 429 threshold may not be hit at exactly 30 or
 * 60 requests. This test fires 65 to exceed the default viewer tier (30/min).
 */

const SHOULD_RUN = !!process.env.RATE_LIMIT_TEST;

test.describe('Rate limiting', () => {
  // Skip unless explicitly enabled
  test.skip(!SHOULD_RUN, 'Skipped by default to avoid WAF/IP bans — set RATE_LIMIT_TEST=1 to enable');

  test('65 rapid GET /vessels triggers at least one 429', async ({ request }) => {
    const statuses: number[] = [];

    for (let i = 0; i < 65; i++) {
      const res = await request.get(`${API}/vessels`);
      statuses.push(res.status());

      // If we already got a 429, verify it and stop early
      if (res.status() === 429) {
        const body = await res.json().catch(() => null);
        expect(body, '429 response should have a JSON body').toBeTruthy();

        // slowapi typically returns { "error": "Rate limit exceeded: ..." }
        const text = JSON.stringify(body);
        const hasInfo =
          text.includes('error') ||
          text.includes('detail') ||
          text.includes('retry') ||
          text.includes('limit');
        expect(hasInfo, '429 body should contain error or retry info').toBe(true);
        break;
      }
    }

    expect(
      statuses,
      `Expected at least one 429 among ${statuses.length} requests, ` +
        `got statuses: ${[...new Set(statuses)].join(', ')}`,
    ).toContain(429);
  });
});
