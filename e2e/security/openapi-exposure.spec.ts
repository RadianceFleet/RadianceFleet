import { test, expect } from '@playwright/test';

/**
 * Check whether OpenAPI / interactive docs endpoints are exposed.
 *
 * For an open-source project these are acceptable, but in a hardened
 * production deployment they might be disabled. These tests document
 * the current state and annotate findings.
 */

test.describe('OpenAPI and docs exposure', () => {
  const endpoints = [
    { path: '/docs', label: 'Swagger UI (/docs)' },
    { path: '/redoc', label: 'ReDoc (/redoc)' },
    { path: '/openapi.json', label: 'OpenAPI spec (/openapi.json)' },
  ];

  for (const { path, label } of endpoints) {
    test(`${label} — document exposure status`, async ({ request }) => {
      const res = await request.get(path);
      const status = res.status();

      if (status === 200) {
        test.info().annotations.push({
          type: 'exposure',
          description: `${label} is ACCESSIBLE (HTTP 200). Acceptable for open-source, review for hardened deployments.`,
        });

        // If openapi.json is accessible, do a basic sanity check
        if (path === '/openapi.json') {
          const body = await res.json();
          expect(body.openapi, 'Should have openapi version field').toBeTruthy();
          expect(body.info, 'Should have info block').toBeTruthy();
          expect(body.paths, 'Should have paths block').toBeTruthy();
        }
      } else if (status === 404) {
        test.info().annotations.push({
          type: 'exposure',
          description: `${label} is DISABLED (HTTP 404). Good for hardened production.`,
        });
      } else {
        // 301/302 redirects, 401/403 auth-gated — all acceptable
        test.info().annotations.push({
          type: 'exposure',
          description: `${label} returned HTTP ${status}. May be auth-gated or redirected.`,
        });
      }

      // Regardless of status, the response should not error out with 5xx
      expect(status, `${label} should not return a server error`).toBeLessThan(500);
    });
  }
});
