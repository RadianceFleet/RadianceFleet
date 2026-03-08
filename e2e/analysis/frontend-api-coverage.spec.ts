import { test, expect } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Frontend API Coverage Analysis
 *
 * Purely static analysis that compares every API path referenced in the
 * frontend source against the production OpenAPI spec.
 *
 * Reports:
 *   - Backend routes covered by frontend code
 *   - Backend routes NOT referenced in frontend (potentially unused from FE perspective)
 *   - Frontend references that don't match any backend route (potential bugs / stale refs)
 *
 * Informational only — does not fail the test.
 */

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FRONTEND_SRC = path.resolve(__dirname, '../../frontend/src');

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

interface FrontendRef {
  /** Normalized path (with {id} placeholders) */
  path: string;
  /** HTTP method, if determinable (defaults to GET) */
  method: string;
  /** Source file where the reference was found */
  sourceFile: string;
}

/**
 * Extract all API path references from frontend source files.
 *
 * Captures:
 *  - apiFetch('/path') and apiFetch(`/path/${expr}`)
 *  - Raw fetch('/api/v1/...') calls
 *  - Literal /api/v1/... href attributes
 *  - fetchEventSource('/api/v1/...')
 */
function extractAllFrontendRefs(srcDir: string): FrontendRef[] {
  const refs: FrontendRef[] = [];
  const files = walkTsFiles(srcDir);

  for (const file of files) {
    const content = fs.readFileSync(file, 'utf-8');
    const relFile = path.relative(srcDir, file);

    // --- apiFetch calls ---
    // apiFetch('/path', { method: 'POST' ... })
    // apiFetch(`/path/${id}`, { method: 'DELETE' })
    const apiFetchRe = /apiFetch[^(]*\(\s*(?:['"](\/?[^'"]+)['"]|`(\/?[^`]+)`)\s*(?:,\s*\{[^}]*method:\s*['"](\w+)['"])?/g;
    let m: RegExpExecArray | null;
    while ((m = apiFetchRe.exec(content)) !== null) {
      const rawPath = m[1] ?? m[2];
      const method = (m[3] ?? 'GET').toUpperCase();
      const p = rawPath.startsWith('/') ? rawPath : '/' + rawPath;
      refs.push({
        path: normalizeApiPath('/api/v1' + p),
        method,
        sourceFile: relFile,
      });
    }

    // --- Raw fetch('/api/v1/...') ---
    const rawFetchRe = /fetch\(\s*(?:['"]([^'"]*\/api\/v1\/[^'"]+)['"]|`([^`]*\/api\/v1\/[^`]+)`)\s*(?:,\s*\{[^}]*method:\s*['"](\w+)['"])?/g;
    while ((m = rawFetchRe.exec(content)) !== null) {
      const rawPath = m[1] ?? m[2];
      const method = (m[3] ?? 'GET').toUpperCase();
      refs.push({
        path: normalizeApiPath(rawPath),
        method,
        sourceFile: relFile,
      });
    }

    // --- Literal href="/api/v1/..." ---
    const hrefRe = /href=\{?\s*[`"']([^`"']*\/api\/v1\/[^`"']+)[`"']/g;
    while ((m = hrefRe.exec(content)) !== null) {
      refs.push({
        path: normalizeApiPath(m[1]),
        method: 'GET',
        sourceFile: relFile,
      });
    }

    // --- fetchEventSource ---
    const sseRe = /fetchEventSource\(\s*(?:['"]([^'"]*\/api\/v1\/[^'"]+)['"]|`([^`]*\/api\/v1\/[^`]+)`)/g;
    while ((m = sseRe.exec(content)) !== null) {
      const rawPath = m[1] ?? m[2];
      refs.push({
        path: normalizeApiPath(rawPath),
        method: 'GET',
        sourceFile: relFile,
      });
    }
  }

  return refs;
}

/** Normalize a path: strip query params, replace dynamic segments with {id}. */
function normalizeApiPath(p: string): string {
  // Strip everything after ? for query params
  let cleaned = p.split('?')[0];
  // Remove template literal prefix like ${...}/api/v1
  cleaned = cleaned.replace(/^\$\{[^}]+\}/, '');
  // Ensure it starts with /api/v1
  const idx = cleaned.indexOf('/api/v1');
  if (idx > 0) cleaned = cleaned.slice(idx);
  return cleaned
    .replace(/\$\{[^}]+\}/g, '{id}')      // template expressions
    .replace(/\/\d+/g, '/{id}')            // numeric segments
    .replace(/\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '/{id}');
}

/** Normalize OpenAPI path params to {id} for comparison. */
function openApiNormalize(p: string): string {
  return p.replace(/\{[^}]+\}/g, '{id}');
}

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

test.describe('Frontend API Coverage', () => {
  test('compare frontend API references against OpenAPI spec', async ({ request }) => {
    // 1. Fetch OpenAPI spec from production
    const res = await request.get('/openapi.json');
    expect(res.ok(), 'Failed to fetch /openapi.json').toBeTruthy();
    const spec = await res.json();

    // 2. Extract all backend routes from the spec, grouped by method
    const backendRoutes = new Map<string, Set<string>>(); // normalized → Set<original>
    const allBackendNormalized = new Map<string, string>(); // normalized → original
    for (const [pathTemplate, methods] of Object.entries(spec.paths ?? {})) {
      const norm = openApiNormalize(pathTemplate);
      allBackendNormalized.set(norm, pathTemplate);
      for (const method of Object.keys(methods as Record<string, unknown>)) {
        const key = `${method.toUpperCase()} ${norm}`;
        if (!backendRoutes.has(key)) {
          backendRoutes.set(key, new Set());
        }
        backendRoutes.get(key)!.add(pathTemplate);
      }
    }

    // 3. Extract all frontend references
    const frontendRefs = extractAllFrontendRefs(FRONTEND_SRC);

    // Deduplicate frontend paths
    const frontendUnique = new Map<string, FrontendRef[]>(); // "METHOD /norm/path" → refs
    for (const ref of frontendRefs) {
      const norm = openApiNormalize(ref.path);
      const key = `${ref.method} ${norm}`;
      if (!frontendUnique.has(key)) {
        frontendUnique.set(key, []);
      }
      frontendUnique.get(key)!.push(ref);
    }

    // Also collect just normalized paths (ignoring method) for a path-only comparison
    const frontendPathsOnly = new Set(
      frontendRefs.map((r) => openApiNormalize(r.path)),
    );
    const backendPathsOnly = new Set(allBackendNormalized.keys());

    // 4. Compute coverage
    const coveredPaths: string[] = [];
    const uncoveredPaths: string[] = [];

    for (const normPath of [...backendPathsOnly].sort()) {
      const original = allBackendNormalized.get(normPath)!;
      if (frontendPathsOnly.has(normPath)) {
        coveredPaths.push(original);
      } else {
        uncoveredPaths.push(original);
      }
    }

    // Frontend refs that don't match any backend path
    const staleRefs: { path: string; method: string; files: string[] }[] = [];
    for (const [key, refs] of frontendUnique.entries()) {
      const normPath = key.split(' ').slice(1).join(' ');
      if (!backendPathsOnly.has(normPath)) {
        staleRefs.push({
          path: refs[0].path,
          method: refs[0].method,
          files: [...new Set(refs.map((r) => r.sourceFile))],
        });
      }
    }

    // 5. Build report
    const totalBackend = backendPathsOnly.size;
    const coveragePct =
      totalBackend > 0 ? ((coveredPaths.length / totalBackend) * 100).toFixed(1) : '0';

    const sections: string[] = [
      '=== Frontend API Coverage Report ===',
      '',
      `Backend paths (unique, normalized): ${totalBackend}`,
      `Frontend references (unique path+method): ${frontendUnique.size}`,
      `Path coverage: ${coveredPaths.length}/${totalBackend} (${coveragePct}%)`,
      '',
      `--- Covered by frontend (${coveredPaths.length}) ---`,
      ...coveredPaths.map((p) => `  ${p}`),
      '',
      `--- NOT referenced in frontend (${uncoveredPaths.length}) ---`,
      '(May be CLI-only, admin-only, API-key-only, or webhook routes)',
      ...uncoveredPaths.map((p) => `  ${p}`),
    ];

    if (staleRefs.length > 0) {
      sections.push(
        '',
        `--- Frontend refs with no matching backend route (${staleRefs.length}) ---`,
        '(May indicate stale code, dynamic path construction, or proxy routes)',
        ...staleRefs.map(
          (r) => `  ${r.method} ${r.path}  (in: ${r.files.join(', ')})`,
        ),
      );
    }

    // Per-file breakdown
    const fileMap = new Map<string, string[]>();
    for (const ref of frontendRefs) {
      if (!fileMap.has(ref.sourceFile)) {
        fileMap.set(ref.sourceFile, []);
      }
      fileMap.get(ref.sourceFile)!.push(`${ref.method} ${ref.path}`);
    }

    sections.push(
      '',
      `--- Per-file reference breakdown (${fileMap.size} files) ---`,
    );
    for (const [file, paths] of [...fileMap.entries()].sort()) {
      const unique = [...new Set(paths)].sort();
      sections.push(`  ${file} (${unique.length} refs)`);
      for (const p of unique) {
        sections.push(`    ${p}`);
      }
    }

    const report = sections.join('\n');
    console.log(report);

    test.info().annotations.push({
      type: 'frontend-api-coverage',
      description: report,
    });

    // Advisory — just ensure the spec was parseable
    expect(totalBackend, 'OpenAPI spec should contain routes').toBeGreaterThan(0);
  });
});
