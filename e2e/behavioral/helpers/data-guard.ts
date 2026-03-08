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

/** Skip the test if element count is 0. */
export function skipIfEmpty(
  test: { skip: (condition: boolean, reason: string) => void },
  count: number,
  label: string,
) {
  test.skip(count === 0, `No ${label} found`);
}
