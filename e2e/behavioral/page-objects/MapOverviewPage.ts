import { Page, Locator } from '@playwright/test';
import { BasePage } from './BasePage';

export class MapOverviewPage extends BasePage {
  readonly mapContainer: Locator;
  readonly tileImages: Locator;
  readonly zoomIn: Locator;
  readonly zoomOut: Locator;
  readonly markers: Locator;
  readonly popup: Locator;

  static readonly LAYER_LABELS = [
    'Corridors',
    'Loitering Zones',
    'Dark Vessels',
    'Coverage Quality',
    'Alert Heatmap',
  ] as const;

  constructor(page: Page) {
    super(page);
    this.mapContainer = page.locator('.leaflet-container');
    this.tileImages = page.locator('.leaflet-tile-pane img[src]');
    this.zoomIn = page.locator('.leaflet-control-zoom-in');
    this.zoomOut = page.locator('.leaflet-control-zoom-out');
    this.markers = page.locator('.leaflet-marker-pane .leaflet-marker-icon');
    this.popup = page.locator('.leaflet-popup-content');
  }

  layerCheckbox(label: string): Locator {
    return this.page.getByRole('main').getByText(label);
  }
}
