import { Page, Response } from '@playwright/test';

const TIMEOUT = 15_000;

function waitForApi(page: Page, pattern: string): Promise<Response> {
  return page.waitForResponse(
    (r) => r.url().includes(pattern) && r.status() < 500,
    { timeout: TIMEOUT },
  );
}

export const waitForAlerts = (page: Page) => waitForApi(page, '/api/v1/alerts?');
export const waitForStats = (page: Page) => waitForApi(page, '/api/v1/stats');
export const waitForVessels = (page: Page) => waitForApi(page, '/api/v1/vessels?');
export const waitForCorridors = (page: Page) => waitForApi(page, '/api/v1/corridors');
export const waitForAlertMap = (page: Page) => waitForApi(page, '/api/v1/alerts/map');
export const waitForMergeCandidates = (page: Page) => waitForApi(page, '/api/v1/merge-candidates?');
export const waitForMergeChains = (page: Page) => waitForApi(page, '/api/v1/merge-chains');
export const waitForAlertTrends = (page: Page) => waitForApi(page, '/api/v1/alerts/trends?');
export const waitForHealthFreshness = (page: Page) => waitForApi(page, '/api/v1/health/data-freshness');
export const waitForCollectionStatus = (page: Page) => waitForApi(page, '/api/v1/health/collection-status');
