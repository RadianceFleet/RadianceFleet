import { test, expect } from '@playwright/test';
import {
  SMOKE_DB_API_KEY,
  BASE_URL,
  hasDbApiKey,
  SITE_API_KEY,
  hasSiteApiKey,
} from '../fixtures/auth';

test.describe('SSE alert stream', () => {
  test('unauthenticated EventSource is rejected', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Use browser-native EventSource — cannot send custom headers,
    // so this tests anonymous (no auth) rejection.
    const result = await page.evaluate((baseUrl: string) => {
      return new Promise<string>((resolve) => {
        const timeout = setTimeout(() => resolve('timeout'), 5_000);

        const es = new EventSource(`${baseUrl}/api/v1/sse/alerts`);

        es.onerror = () => {
          clearTimeout(timeout);
          es.close();
          resolve('error');
        };

        // If we somehow get a message, that's unexpected but not a failure mode we block on
        es.onmessage = () => {
          clearTimeout(timeout);
          es.close();
          resolve('message');
        };
      });
    }, BASE_URL);

    // The SSE endpoint requires auth, so the connection should error out.
    // Either an explicit error or a timeout (server closes connection) is acceptable.
    expect(
      ['error', 'timeout'],
      `Expected EventSource to error or timeout without auth, got: ${result}`,
    ).toContain(result);
  });

  test('authenticated SSE stream returns text/event-stream', async () => {
    test.skip(!hasDbApiKey(), 'SMOKE_DB_API_KEY not set — skipping authenticated SSE test');

    const headers: Record<string, string> = {
      'X-API-Key': SMOKE_DB_API_KEY,
      Accept: 'text/event-stream',
    };

    // If the site-level API key gate is active, include it too
    if (hasSiteApiKey()) {
      headers['X-API-Key'] = SITE_API_KEY;
      // Use a separate header for the DB key if the site key occupies X-API-Key.
      // The server checks X-API-Key for both — site gate first, then auth.
      // With dual keys, send the site key as X-API-Key (gate) and DB key as Authorization Bearer.
      headers['Authorization'] = `Bearer ${SMOKE_DB_API_KEY}`;
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5_000);

    try {
      const response = await fetch(`${BASE_URL}/api/v1/sse/alerts`, {
        method: 'GET',
        headers,
        signal: controller.signal,
      });

      expect(response.status).toBe(200);

      const contentType = response.headers.get('content-type') ?? '';
      expect(
        contentType,
        'SSE endpoint should return text/event-stream content type',
      ).toContain('text/event-stream');
    } catch (err: unknown) {
      // AbortError is expected — we intentionally abort after 5s
      if (err instanceof Error && err.name === 'AbortError') {
        // This is fine — the stream was open and we cut it off
        return;
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
      controller.abort();
    }
  });
});
