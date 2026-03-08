/**
 * Auth fixture — provides optional read-only API key for authenticated tests.
 *
 * Two auth layers:
 *   1. Global gate (SITE_API_KEY → X-API-Key header) — handled by playwright.config.ts extraHTTPHeaders
 *   2. Per-endpoint auth (SMOKE_DB_API_KEY) — for endpoints with require_auth dependency
 */

export const SMOKE_DB_API_KEY = process.env.SMOKE_DB_API_KEY ?? '';
export const SITE_API_KEY = process.env.SITE_API_KEY ?? '';
export const BASE_URL = process.env.BASE_URL ?? 'https://radiancefleet.com';

export const hasDbApiKey = (): boolean => SMOKE_DB_API_KEY.length > 0;
export const hasSiteApiKey = (): boolean => SITE_API_KEY.length > 0;
