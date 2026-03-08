import { Page, Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class VesselSearchPage extends BasePage {
  readonly searchInput: Locator;
  readonly flagInput: Locator;
  readonly typeInput: Locator;
  readonly advancedToggle: Locator;
  readonly minDwt: Locator;
  readonly maxDwt: Locator;
  readonly emptyState: Locator;

  constructor(page: Page) {
    super(page);
    this.searchInput = page.getByPlaceholder('Search MMSI, IMO, or name...');
    this.flagInput = page.getByPlaceholder('Flag (e.g. PA)');
    this.typeInput = page.getByPlaceholder('Vessel type');
    this.advancedToggle = page.getByRole('button', { name: /Advanced Filters|Hide Filters/ });
    this.minDwt = page.getByPlaceholder('Min DWT');
    this.maxDwt = page.getByPlaceholder('Max DWT');
    this.emptyState = page.getByText('Search for vessels');
  }
}
