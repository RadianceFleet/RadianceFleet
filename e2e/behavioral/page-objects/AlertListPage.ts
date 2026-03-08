import { Page, Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class AlertListPage extends BasePage {
  readonly minScore: Locator;
  readonly statusSelect: Locator;
  readonly vesselName: Locator;
  readonly dateFrom: Locator;
  readonly dateTo: Locator;
  readonly patternsToggle: Locator;
  readonly prevButton: Locator;
  readonly nextButton: Locator;
  readonly alertLinks: Locator;
  readonly vesselLinks: Locator;

  constructor(page: Page) {
    super(page);
    this.minScore = page.getByPlaceholder('Min score');
    this.statusSelect = page.locator('select');
    this.vesselName = page.getByPlaceholder('Vessel name');
    this.dateFrom = page.locator('input[title="Date from"]');
    this.dateTo = page.locator('input[title="Date to"]');
    this.patternsToggle = page.getByText('Patterns only');
    this.prevButton = page.getByRole('button', { name: 'Prev' });
    this.nextButton = page.getByRole('button', { name: 'Next' });
    this.alertLinks = page.locator('a[href*="/alerts/"]');
    this.vesselLinks = page.locator('a[href*="/vessels/"]');
  }

  sortHeader(label: string): Locator {
    return this.page.locator('th', { hasText: label });
  }

  pageInfo(): Locator {
    return this.page.getByText(/Page \d+ of \d+/);
  }

  totalAlerts(): Locator {
    return this.page.getByText(/\d+ alerts? total/);
  }
}
