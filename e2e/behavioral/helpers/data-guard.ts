import { APIRequestContext, TestInfo } from '@playwright/test';

/**
 * Fetch the first item's ID from an API list endpoint.
 * Handles both `[{id}]` and `{items:[{id}]}` response shapes.
 */
export async function fetchFirstId(
  request: APIRequestContext,
  endpoint: string,
  idField = 'id',
): Promise<string | null> {
  try {
    const base = endpoint.startsWith('/') ? endpoint : `/api/v1/${endpoint}`;
    const res = await request.get(`${base}?limit=1`);
    if (res.status() >= 400) return null;
    const json = await res.json();
    const items = Array.isArray(json) ? json : json?.items ?? [];
    if (items.length === 0) return null;
    const val = items[0][idField];
    return val != null ? String(val) : null;
  } catch {
    return null;
  }
}

/** Skip the test if id is null/undefined — data not available. */
export function skipIfNoData(
  test: { skip: (condition: boolean, reason: string) => void },
  id: string | null | undefined,
  label: string,
) {
  test.skip(!id, `No ${label} available`);
}

/** Add an advisory annotation (informational, not a failure). */
export function advisoryReport(testInfo: TestInfo, message: string) {
  testInfo.annotations.push({ type: 'advisory', description: message });
}

/**
 * Fetch the first N item IDs from an API list endpoint.
 * Returns up to `count` IDs (may be fewer if data is scarce).
 */
export async function fetchMultipleIds(
  request: APIRequestContext,
  endpoint: string,
  idField = 'id',
  count = 2,
): Promise<string[]> {
  try {
    const base = endpoint.startsWith('/') ? endpoint : `/api/v1/${endpoint}`;
    const res = await request.get(`${base}?limit=${count}`);
    if (res.status() >= 400) return [];
    const json = await res.json();
    const items = Array.isArray(json) ? json : json?.items ?? [];
    return items
      .map((item: Record<string, unknown>) => item[idField])
      .filter((v: unknown) => v != null)
      .map(String)
      .slice(0, count);
  } catch {
    return [];
  }
}

/**
 * Fetch the first alert that has a non-null vessel_id.
 * Critical for tests that click from alert detail → vessel detail,
 * since AlertDetail.tsx conditionally renders the vessel link.
 */
export async function fetchFirstAlertWithVessel(
  request: APIRequestContext,
): Promise<{ alertId: string; vesselId: string } | null> {
  try {
    const res = await request.get('/api/v1/alerts?limit=5');
    if (res.status() >= 400) return null;
    const json = await res.json();
    const items = Array.isArray(json) ? json : json?.items ?? [];
    for (const item of items) {
      if (item.vessel_id != null && item.gap_event_id != null) {
        return {
          alertId: String(item.gap_event_id),
          vesselId: String(item.vessel_id),
        };
      }
    }
    return null;
  } catch {
    return null;
  }
}

/** Fetch the first dark vessel detection ID. */
export async function fetchFirstDarkVessel(
  request: APIRequestContext,
): Promise<string | null> {
  return fetchFirstId(request, 'dark-vessels', 'detection_id');
}

/** Fetch the first STS event ID. */
export async function fetchFirstStsEvent(
  request: APIRequestContext,
): Promise<string | null> {
  return fetchFirstId(request, 'sts-events', 'sts_id');
}

/** Skip the test if element count is 0. */
export function skipIfEmpty(
  test: { skip: (condition: boolean, reason: string) => void },
  count: number,
  label: string,
) {
  test.skip(count === 0, `No ${label} found`);
}
