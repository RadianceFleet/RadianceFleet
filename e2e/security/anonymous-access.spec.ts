import { test, expect } from '@playwright/test';

const API = '/api/v1';

/**
 * Verify that auth-protected endpoints reject anonymous requests.
 *
 * All tests are strictly non-mutating (GET and OPTIONS only).
 * We create a fresh request context WITHOUT the default X-API-Key header
 * so requests are truly anonymous. If the server has a global API-key gate
 * (RADIANCEFLEET_API_KEY), anonymous requests hit that gate first — that is
 * fine, it still proves unauthenticated users are blocked.
 */

const SENSITIVE_PATTERNS = /password|secret|token|Traceback|File "/i;

test.describe('Anonymous access — auth-protected GET endpoints', () => {
  const protectedEndpoints = [
    { path: `${API}/admin/analysts`, label: 'admin analysts' },
    { path: `${API}/admin/api-keys`, label: 'admin api-keys' },
    { path: `${API}/admin/webhooks`, label: 'admin webhooks' },
    // audit-log is not a GET endpoint (returns 404), omitted
    { path: `${API}/alerts/my`, label: 'alerts my' },
    { path: `${API}/alerts/saved-filters`, label: 'alerts saved-filters' },
    { path: `${API}/sse/alerts`, label: 'SSE alerts' },
  ];

  for (const { path, label } of protectedEndpoints) {
    test(`GET ${label} rejects anonymous request`, async ({ playwright }) => {
      // Create a context with NO default headers — truly anonymous
      const anonContext = await playwright.request.newContext({
        baseURL: process.env.BASE_URL ?? 'https://radiancefleet.com',
      });

      try {
        const res = await anonContext.get(path);
        const status = res.status();

        expect(
          [401, 403],
          `Expected 401 or 403 for anonymous GET ${path}, got ${status}`,
        ).toContain(status);

        // Response body must not leak sensitive information
        const body = await res.text();
        expect(body).not.toMatch(SENSITIVE_PATTERNS);
      } finally {
        await anonContext.dispose();
      }
    });
  }
});

test.describe('Anonymous access — mutation endpoints exist (OPTIONS only)', () => {
  const mutationEndpoints = [
    { path: `${API}/alerts/1/status`, label: 'alert status' },
    { path: `${API}/watchlist`, label: 'watchlist' },
    { path: `${API}/vessels/merge`, label: 'vessels merge' },
  ];

  for (const { path, label } of mutationEndpoints) {
    test(`OPTIONS ${label} returns without sensitive data`, async ({ playwright }) => {
      const anonContext = await playwright.request.newContext({
        baseURL: process.env.BASE_URL ?? 'https://radiancefleet.com',
      });

      try {
        const res = await anonContext.fetch(path, { method: 'OPTIONS' });
        const status = res.status();

        // OPTIONS should return 200, 204, or 405 — anything in the 2xx/4xx range is fine.
        // A 5xx would indicate a server error worth investigating.
        expect(status, `OPTIONS ${path} returned server error ${status}`).toBeLessThan(500);

        const body = await res.text();
        expect(body).not.toMatch(SENSITIVE_PATTERNS);
      } finally {
        await anonContext.dispose();
      }
    });
  }
});
