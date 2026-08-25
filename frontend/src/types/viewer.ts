export interface CanvasTransform {
  scale: number; // 0.25 to 5.0
  translateX: number;
  translateY: number;
  rotation: number; // 0, 90, 180, 270
}

export interface FilterSettings {
  brightness: number; // 0 to 200, default 100
  contrast: number; // 0 to 300, default 100
  invert: boolean; // default false
}

export interface CardViewerSettings {
  linked: boolean;
  transformFront: CanvasTransform;
  transformBack: CanvasTransform;
  filtersFront: FilterSettings;
  filtersBack: FilterSettings;
}
