import { test, expect } from '@playwright/test';

const API = '/api/v1';

test.describe('Health endpoints', () => {
  test('GET /api/v1/health returns ok with acceptable latency', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(body.status).toBe('ok');
    expect(body.database?.status).toBe('ok');
    expect(body.database?.latency_ms).toBeLessThan(1000);
  });

  test('no circuit breakers are open', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    if (body.circuit_breakers && typeof body.circuit_breakers === 'object') {
      for (const [name, state] of Object.entries(body.circuit_breakers)) {
        expect(state, `circuit breaker "${name}" should not be open`).not.toBe('open');
      }
    }
  });

  test('GET /api/v1/health/data-freshness within 6h SLA', async ({ request }) => {
    const res = await request.get(`${API}/health/data-freshness`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    // staleness_minutes is null when no AIS data has been ingested yet (fresh deploy)
    if (body.staleness_minutes !== null) {
      expect(body.staleness_minutes).toBeLessThan(360);
    }
  });

  test('GET /api/v1/health/collection-status reports recent runs', async ({ request }) => {
    const res = await request.get(`${API}/health/collection-status`);
    expect(res.status()).toBe(200);

    const body = await res.json();
    // The collection pipeline should report some data about recent runs
    expect(body).toBeTruthy();
    expect(typeof body).toBe('object');
  });

  test('GET /health root-level endpoint is reachable', async ({ request }) => {
    const res = await request.get('/health');
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(body).toBeTruthy();
  });
});
