import { test, expect } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { STATIC_PAGES } from '../fixtures/routes.generated';

/**
 * Dead-route analysis — two-layer approach (runtime + static).
 *
 * Layer 1: Visit every STATIC_PAGE and intercept all GET /api/v1/* requests
 *          that the frontend actually fires during page loads.
 *
 * Layer 2: Statically grep frontend source for API path references, then diff
 *          against the OpenAPI spec to find backend GET routes with no frontend
 *          consumer.
 *
 * Results are advisory (annotations), not hard failures.
 */

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_SRC = path.resolve(__dirname, '../../frontend/src');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Replace numeric/UUID path segments with `{id}` for grouping. */
function normalizePath(url: string): string {
  const parsed = new URL(url);
  return parsed.pathname
    .replace(/\/\d+/g, '/{id}')
    .replace(/\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '/{id}');
}

/** Recursively collect all .ts / .tsx files under a directory. */
function walkTsFiles(dir: string): string[] {
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory() && entry.name !== 'node_modules') {
      results.push(...walkTsFiles(full));
    } else if (/\.tsx?$/.test(entry.name)) {
      results.push(full);
    }
  }
  return results;
}

/**
 * Extract API path segments referenced in frontend source.
 *
 * Looks for:
 *  - Literal `/api/v1/…` strings
 *  - Paths passed to apiFetch (which prepends `/api/v1`)
 *  - Template literal path segments used with apiFetch
 */
function extractFrontendPaths(srcDir: string): Set<string> {
  const paths = new Set<string>();
  const files = walkTsFiles(srcDir);

  // Match literal /api/v1/… paths
  const literalRe = /\/api\/v1(\/[^\s'"`,)}\]]+)/g;
  // Match apiFetch('/some/path…') or apiFetch(`/some/path…`)
  const apiFetchSingleRe = /apiFetch[^(]*\(\s*['"](\/?[^'"]+)['"]/g;
  const apiFetchBacktickRe = /apiFetch[^(]*\(\s*`(\/?[^`]+)`/g;
  // Match fetch('…/api/v1/…')
  const rawFetchRe = /fetch\(\s*[`'"][^`'"]*\/api\/v1(\/[^`'"]+)[`'"]/g;

  for (const file of files) {
    const content = fs.readFileSync(file, 'utf-8');

    for (const re of [literalRe, rawFetchRe]) {
      let m: RegExpExecArray | null;
      while ((m = re.exec(content)) !== null) {
        paths.add(normalizeStaticPath('/api/v1' + m[1]));
      }
    }

    for (const re of [apiFetchSingleRe, apiFetchBacktickRe]) {
      let m: RegExpExecArray | null;
      while ((m = re.exec(content)) !== null) {
        const p = m[1].startsWith('/') ? m[1] : '/' + m[1];
        paths.add(normalizeStaticPath('/api/v1' + p));
      }
    }
  }

  return paths;
}

/** Normalize a static path string: strip query params, collapse template vars and IDs. */
function normalizeStaticPath(p: string): string {
  return p
    .split('?')[0]
    .replace(/\$\{[^}]+\}/g, '{id}')   // template expressions → {id}
    .replace(/\/\d+/g, '/{id}')         // numeric segments → {id}
    .replace(/\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '/{id}');
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Dead-route analysis', () => {
  test('Layer 1 — runtime API call interception', async ({ page }) => {
    const apiCalls = new Set<string>();

    page.on('request', (req) => {
      const url = req.url();
      if (req.method() === 'GET' && url.includes('/api/v1/')) {
        apiCalls.add(normalizePath(url));
      }
    });

    for (const { path: route } of STATIC_PAGES) {
      await page.goto(route);
      await page.waitForLoadState('domcontentloaded');
      // Brief pause to let initial API calls fire
      await page.waitForTimeout(1500);
    }

    const sorted = [...apiCalls].sort();
    const report = [
      `Runtime API calls observed across ${STATIC_PAGES.length} pages:`,
      `Total unique normalized paths: ${sorted.length}`,
      '',
      ...sorted.map((p) => `  ${p}`),
    ].join('\n');

    console.log(report);
    test.info().annotations.push({
      type: 'runtime-api-calls',
      description: report,
    });

    // Sanity: we expect at least some API calls were made
    expect(sorted.length, 'Expected at least one API call during page loads').toBeGreaterThan(0);
  });

  test('Layer 2 — static analysis: backend GET routes vs frontend references', async ({
    request,
  }) => {
    // 1. Fetch OpenAPI spec
    const res = await request.get('/openapi.json');
    expect(res.ok(), 'Failed to fetch /openapi.json').toBeTruthy();
    const spec = await res.json();

    // Extract all GET paths from the spec
    const backendGetPaths = new Set<string>();
    for (const [pathTemplate, methods] of Object.entries(spec.paths ?? {})) {
      if ((methods as Record<string, unknown>)['get']) {
        backendGetPaths.add(pathTemplate);
      }
    }

    // 2. Extract frontend references
    const frontendPaths = extractFrontendPaths(FRONTEND_SRC);

    // 3. Diff: backend GET routes NOT referenced in frontend
    //    We need to normalize backend paths for comparison (OpenAPI uses {param} style)
    function openApiToNormalized(p: string): string {
      return p.replace(/\{[^}]+\}/g, '{id}');
    }

    const normalizedFrontendPaths = new Set([...frontendPaths].map(openApiToNormalized));

    const unreferenced: string[] = [];
    const referenced: string[] = [];

    for (const bp of [...backendGetPaths].sort()) {
      const norm = openApiToNormalized(bp);
      if (normalizedFrontendPaths.has(norm)) {
        referenced.push(bp);
      } else {
        unreferenced.push(bp);
      }
    }

    // 4. Frontend paths that don't match any backend route (potential stale refs)
    const normalizedBackendPaths = new Set([...backendGetPaths].map(openApiToNormalized));
    const staleRefs = [...frontendPaths]
      .filter((fp) => !normalizedBackendPaths.has(openApiToNormalized(fp)))
      .sort();

    // 5. Build report
    const report = [
      '=== Dead-Route Analysis (Static) ===',
      '',
      `Backend GET routes total: ${backendGetPaths.size}`,
      `Frontend path references found: ${frontendPaths.size}`,
      '',
      `--- Referenced in frontend (${referenced.length}) ---`,
      ...referenced.map((p) => `  ${p}`),
      '',
      `--- NOT referenced in frontend (${unreferenced.length}) ---`,
      '(These may be CLI-only, API-key-only, or webhook routes — advisory only)',
      ...unreferenced.map((p) => `  ${p}`),
      '',
      `--- Frontend refs with no matching backend route (${staleRefs.length}) ---`,
      '(May indicate stale code or dynamic path construction)',
      ...staleRefs.map((p) => `  ${p}`),
    ].join('\n');

    console.log(report);
    test.info().annotations.push({
      type: 'dead-route-analysis',
      description: report,
    });

    // Advisory — do NOT fail the test. Just ensure the spec was parseable.
    expect(backendGetPaths.size, 'OpenAPI spec should contain GET routes').toBeGreaterThan(0);
  });
});
