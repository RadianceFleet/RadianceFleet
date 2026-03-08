import { defineConfig, devices } from '@playwright/test';

/**
 * Remote E2E tests for radiancefleet.com.
 *
 * Environment variables:
 *   BASE_URL          – target (default: https://radiancefleet.com)
 *   SITE_API_KEY      – global gate key (RADIANCEFLEET_API_KEY on server), optional
 *   SMOKE_DB_API_KEY  – DB-backed read-only API key for authenticated tests, optional
 */

const BASE_URL = process.env.BASE_URL ?? 'https://radiancefleet.com';

// If the global API-key gate is active, all requests need this header.
const siteApiKey = process.env.SITE_API_KEY;
const extraHTTPHeaders: Record<string, string> = {};
if (siteApiKey) {
  extraHTTPHeaders['X-API-Key'] = siteApiKey;
}

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  timeout: 60_000,
  expect: { timeout: 15_000 },

  reporter: process.env.CI
    ? [['html', { open: 'never' }], ['github']]
    : [['html', { open: 'on-failure' }]],

  use: {
    baseURL: BASE_URL,
    extraHTTPHeaders,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'on-first-retry',
  },

  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 7'] },
    },
    {
      name: 'mobile-safari',
      use: { ...devices['iPhone 14'] },
    },
  ],
});
