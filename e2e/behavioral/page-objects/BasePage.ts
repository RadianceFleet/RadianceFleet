import { Page, Locator, expect } from '@playwright/test';

export class BasePage {
  readonly page: Page;
  readonly sidebar: Locator;
  readonly heading: Locator;

  constructor(page: Page) {
    this.page = page;
    this.sidebar = page.locator('nav');
    this.heading = page.locator('h1, h2, h3, [role="heading"]').first();
  }

  /** Wait for DOM content + ensure no error boundary rendered. */
  async waitForContentLoad() {
    await this.page.waitForLoadState('domcontentloaded');
    // Brief settle for React hydration
    await this.page.waitForTimeout(300);
  }

  /** Assert no React error boundary or uncaught error is visible. */
  async assertNoErrors() {
    const errorBoundary = this.page.getByText('Something went wrong');
    await expect(errorBoundary).not.toBeVisible({ timeout: 2000 });
  }
}
