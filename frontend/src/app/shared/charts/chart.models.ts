/**
 * The shared data shape for the analytics charts (FS-15 §7, IP-17 T-13).
 *
 * The charts are deliberately dumb: they receive already-computed, already-formatted points and draw
 * them. No chart derives a metric, applies a filter, or decides what a null means — that would put a
 * second, silently divergent interpretation of the data next to the Backend's (FS-15 §5).
 */
export interface ChartPoint {
  /** The x-axis label, already formatted for the active bucket size (hour / day / week). */
  label: string;

  /**
   * The plotted value, or `null` when the bucket genuinely has no measurement.
   *
   * `null` is drawn as a **gap**, never as zero: a bucket with no eligible latency sample has not
   * been measured at 0 ms (FS-15 §5.4). A real, measured zero is `0`, and the two must stay visually
   * distinct.
   */
  value: number | null;

  /** The tooltip text, pre-formatted with its unit by the page (e.g. "12 detections", "2.4 s"). */
  tooltip: string;
}

/** One slice/row of the confidence donut (FS-15 §5.2). */
export interface DonutSlice {
  label: string;
  value: number;

  /** A CSS custom-property name or colour used for this slice's ring segment and legend dot. */
  color: string;
}

/** One bar of the Branch-density chart (FS-15 §5.3). */
export interface BarDatum {
  label: string;
  value: number;
  tooltip: string;
}

/**
 * A "nice" axis maximum at or above `value` — 1, 2 or 5 × a power of ten — so the y-axis reads in
 * round numbers instead of ending on an arbitrary data-driven maximum.
 *
 * Returns 1 for an all-zero series so the axis still has a usable scale and the baseline is visible.
 */
export function niceAxisMaximum(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    return 1;
  }

  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;

  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

/**
 * Evenly spaced tick values from 0 to `maximum` inclusive.
 *
 * `count` is the number of *intervals*, so `axisTicks(100, 4)` yields `[0, 25, 50, 75, 100]`.
 */
export function axisTicks(maximum: number, count = 4): number[] {
  return Array.from({ length: count + 1 }, (_, index) => (maximum / count) * index);
}

/**
 * The indexes of the x-axis labels that should actually be rendered, so a 90-bucket series does not
 * print 90 overlapping labels. The first and last are always kept, and the rest are sampled evenly.
 */
export function labelIndexes(total: number, maximum = 7): number[] {
  if (total <= maximum) {
    return Array.from({ length: total }, (_, index) => index);
  }

  const step = (total - 1) / (maximum - 1);
  const indexes = Array.from({ length: maximum }, (_, index) => Math.round(index * step));
  return [...new Set(indexes)];
}
