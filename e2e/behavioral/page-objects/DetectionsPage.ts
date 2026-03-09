import { Page, Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class DetectionsPage extends BasePage {
  readonly spoofingTab: Locator;
  readonly loiteringTab: Locator;
  readonly stsChainsTab: Locator;
  readonly activeTable: Locator;
  readonly emptyState: Locator;

  constructor(page: Page) {
    super(page);
    this.spoofingTab = page.getByRole('button', { name: /spoofing/i }).or(
      page.getByRole('tab', { name: /spoofing/i }),
    );
    this.loiteringTab = page.getByRole('button', { name: /loitering/i }).or(
      page.getByRole('tab', { name: /loitering/i }),
    );
    this.stsChainsTab = page.getByRole('button', { name: /STS|chains/i }).or(
      page.getByRole('tab', { name: /STS|chains/i }),
    );
    this.activeTable = page.locator('table:visible');
    this.emptyState = page.getByText(/no.*detected|no.*results|no.*found/i);
  }
}
