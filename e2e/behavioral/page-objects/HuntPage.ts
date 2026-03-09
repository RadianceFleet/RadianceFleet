import { Page, Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class HuntPage extends BasePage {
  readonly missionsTab: Locator;
  readonly targetsTab: Locator;
  readonly createTargetBtn: Locator;
  readonly createMissionBtn: Locator;

  constructor(page: Page) {
    super(page);
    this.missionsTab = page.getByRole('button', { name: /missions/i }).or(
      page.getByRole('tab', { name: /missions/i }),
    );
    this.targetsTab = page.getByRole('button', { name: /targets/i }).or(
      page.getByRole('tab', { name: /targets/i }),
    );
    this.createTargetBtn = page.getByRole('button', { name: /new.*target/i });
    this.createMissionBtn = page.getByRole('button', { name: /new.*mission/i });
  }
}
