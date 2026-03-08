import { test, expect } from '@playwright/test';

/** Endpoints that require JWT / analyst auth — skip in anonymous smoke tests. */
const AUTHED_GETS = [
  '/alerts/my',
  '/alerts/saved-filters',
  '/admin/analysts',
  '/admin/api-keys',
  '/admin/webhooks',
  '/admin/audit-log',
];

/** Endpoints that use SSE or require query tokens — not suitable for JSON smoke. */
const SKIP_PATTERNS = [
  '/sse/',
  '/unsubscribe',
  '/subscribe/confirm',
];

test.describe('Dynamic API endpoint smoke tests', () => {
  let getEndpoints: string[] = [];

  test.beforeAll(async ({ request }) => {
    const res = await request.get('/openapi.json');
    expect(res.status(), 'OpenAPI spec must be reachable').toBe(200);

    const spec = await res.json();
    const paths: Record<string, Record<string, unknown>> = spec.paths ?? {};

    const candidates: string[] = [];
    for (const [path, methods] of Object.entries(paths)) {
      if (!methods.get) continue;
      // Skip parameterized paths
      if (path.includes('{')) continue;
      // Skip auth-required endpoints
      if (AUTHED_GETS.some((a) => path.endsWith(a))) continue;
      // Skip SSE / token-gated
      if (SKIP_PATTERNS.some((p) => path.includes(p))) continue;

      candidates.push(path);
    }

    getEndpoints = candidates.sort();
  });

  test('openapi.json yields at least 10 GET endpoints', () => {
    expect(getEndpoints.length).toBeGreaterThanOrEqual(10);
  });

  test('all discovered GET endpoints return < 500 with valid JSON', async ({ request }) => {
    expect(getEndpoints.length).toBeGreaterThan(0);

    const failures: string[] = [];

    for (const path of getEndpoints) {
      const url = path.includes('?') ? path : `${path}?limit=1`;
      const res = await request.get(url);

      if (res.status() >= 500) {
        failures.push(`${path} => status ${res.status()}`);
        continue;
      }

      const contentType = res.headers()['content-type'] ?? '';
      // Some endpoints may return non-JSON (CSV, binary) — only validate JSON ones
      if (!contentType.includes('json')) continue;

      const text = await res.text();

      if (text.includes('Traceback')) {
        failures.push(`${path} => contains Python Traceback`);
        continue;
      }

      // Verify parseable JSON
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        failures.push(`${path} => invalid JSON`);
        continue;
      }

      // For list endpoints: expect array or object with items/results key
      if (Array.isArray(parsed)) {
        // valid list shape
      } else if (parsed && typeof parsed === 'object') {
        // valid object shape (could be {items: [...]}, {results: [...]}, or plain object)
      }
    }

    if (failures.length > 0) {
      throw new Error(
        `${failures.length} endpoint(s) failed:\n${failures.join('\n')}`,
      );
    }
  });
});
