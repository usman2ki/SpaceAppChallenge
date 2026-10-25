import { interpolateRgbBasis } from "d3-interpolate";

export const PLATFORM_COLOR: Record<string, string> = {
  Terra: "var(--s-terra)",
  Aqua: "var(--s-aqua)",
  "Suomi-NPP": "var(--s-snpp)",
  "NOAA-20": "var(--s-noaa20)",
  "NOAA-21": "var(--s-noaa21)",
};

export const PLATFORM_INSTRUMENT: Record<string, string> = {
  Terra: "MODIS",
  Aqua: "MODIS",
  "Suomi-NPP": "VIIRS",
  "NOAA-20": "VIIRS",
  "NOAA-21": "VIIRS",
};

// Heat ramp: used only for detection-based quantities.
const ramp = interpolateRgbBasis(["#f6e3a1", "#f0a04b", "#d9481c", "#9e1b32", "#4a1942"]);

/** log-scaled colour for a positive value given the max of the view. */
export function heatColor(v: number, max: number): string {
  if (!(v > 0) || !(max > 0)) return "#f6e3a1";
  const t = Math.log1p(v) / Math.log1p(max);
  return ramp(Math.min(1, Math.max(0.04, t)));
}

export function heatStops(n = 6): string[] {
  return Array.from({ length: n }, (_, i) => ramp(i / (n - 1)));
}

export type CellStatus =
  | "detections"
  | "observed_none"
  | "partially_observed_none"
  | "unobserved"
  | "none_reported_coverage_unknown"
  | "not_queried";

export const STATUS_TEXT: Record<string, string> = {
  detections: "Detections reported",
  observed_none: "Observed, no detections",
  partially_observed_none: "Partly observed, no detections",
  unobserved: "Not observed (clouds or no overpass)",
  none_reported_coverage_unknown: "No detections reported; coverage unknown",
  not_queried: "No data imported for this period",
};

export const SIGNAL_TEXT: Record<string, string> = {
  unusually_high: "Unusually high for the season (FDR-controlled)",
  unusually_low: "Unusually low for the season (FDR-controlled)",
  typical: "Within the usual range for the season",
  insufficient_history: "Not enough other years to judge",
  not_observed: "Not observed",
  too_sparse: "Baseline too sparse to test",
};
