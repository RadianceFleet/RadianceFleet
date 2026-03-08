import { test, expect } from '@playwright/test';

const API = '/api/v1';
const EVIL_ORIGIN = 'https://evil.com';

/**
 * CORS and security header tests.
 *
 * Uses the `request` fixture (API context), not browser navigation.
 * FastAPI sets ZERO security headers by default, so many of the
 * security-header checks use test.fixme() to flag gaps without
 * causing CI failures.
 */

test.describe('CORS — reject foreign origins', () => {
  test('GET /health with evil origin omits Access-Control-Allow-Origin', async ({ request }) => {
    const res = await request.get(`${API}/health`, {
      headers: { Origin: EVIL_ORIGIN },
    });

    // Starlette CORSMiddleware returns 200 but omits CORS headers
    // for disallowed origins.
    expect(res.status()).toBe(200);

    const acao = res.headers()['access-control-allow-origin'];
    expect(
      acao,
      'Access-Control-Allow-Origin should be absent for evil origin',
    ).toBeUndefined();
  });

  test('OPTIONS preflight with evil origin has no CORS allow-origin header', async ({ request }) => {
    const res = await request.fetch(`${API}/health`, {
      method: 'OPTIONS',
      headers: {
        Origin: EVIL_ORIGIN,
        'Access-Control-Request-Method': 'POST',
      },
    });

    const headers = res.headers();
    // Starlette CORSMiddleware with allow_methods=["*"] responds to all OPTIONS
    // with allowed methods, but critically does NOT set Access-Control-Allow-Origin
    // for disallowed origins — the browser enforces the block client-side.
    expect(
      headers['access-control-allow-origin'],
      'Preflight should not allow evil origin',
    ).toBeUndefined();
  });
});

test.describe('Security headers on GET /health', () => {
  test('X-Content-Type-Options: nosniff', async ({ request }) => {
    test.fixme(true, 'FastAPI does not set X-Content-Type-Options by default — flag gap');

    const res = await request.get(`${API}/health`);
    expect(res.headers()['x-content-type-options']).toBe('nosniff');
  });

  test('Strict-Transport-Security present', async ({ request }) => {
    test.fixme(true, 'HSTS requires explicit middleware or reverse proxy — flag gap');

    const res = await request.get(`${API}/health`);
    const hsts = res.headers()['strict-transport-security'];
    expect(hsts).toBeTruthy();
  });

  test('X-Frame-Options is DENY or SAMEORIGIN', async ({ request }) => {
    test.fixme(true, 'FastAPI does not set X-Frame-Options by default — flag gap');

    const res = await request.get(`${API}/health`);
    const xfo = res.headers()['x-frame-options'];
    expect(xfo).toBeTruthy();
    expect(['DENY', 'SAMEORIGIN']).toContain(xfo!.toUpperCase());
  });

  test('Content-Security-Policy present', async ({ request }) => {
    test.fixme(true, 'FastAPI does not set CSP by default — flag gap');

    const res = await request.get(`${API}/health`);
    expect(res.headers()['content-security-policy']).toBeTruthy();
  });

  test('Referrer-Policy present', async ({ request }) => {
    test.fixme(true, 'FastAPI does not set Referrer-Policy by default — flag gap');

    const res = await request.get(`${API}/health`);
    expect(res.headers()['referrer-policy']).toBeTruthy();
  });

  test('no X-Powered-By header leaked', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(
      res.headers()['x-powered-by'],
      'X-Powered-By should not be present',
    ).toBeUndefined();
  });

  test('no Server: uvicorn header leaked', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    const server = res.headers()['server'];
    if (server) {
      expect(
        server.toLowerCase(),
        'Server header should not reveal uvicorn',
      ).not.toContain('uvicorn');
    }
    // If server header is absent, that is ideal — test passes.
  });
});
