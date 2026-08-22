import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ChartPoint, axisTicks, labelIndexes, niceAxisMaximum } from './chart.models';

/** The internal plot geometry, in SVG user units. The SVG is stretched to its container's width, so
 *  these are a coordinate system rather than pixels — every stroke carries `non-scaling-stroke` so
 *  line weights stay true regardless of how far the plot is stretched. */
const PLOT_WIDTH = 1000;
const PLOT_HEIGHT = 300;

/**
 * A dependency-free line/area chart (FS-15 §7, IP-17 T-13; Stitch "Detections Over Time" and
 * "Avg. Response Time" cards).
 *
 * **Why not a charting library.** `frontend/package.json` carries only Angular, rxjs, tslib and
 * zone.js — no UI framework at all, by standing project convention (`styles.css` header). Four small
 * charts do not justify reversing that, so this renders plain SVG (IP-17 §1.5).
 *
 * **Responsiveness.** The plot area is an SVG with `preserveAspectRatio="none"`, stretched to the
 * card's width at a fixed height, while the axis labels are ordinary HTML positioned around it. That
 * keeps the labels crisp and unstretched at every viewport width — the failure mode of putting text
 * inside a non-uniformly scaled SVG.
 *
 * **Nulls are gaps, never zeros.** A `null` value breaks the path (FS-15 §5.4): a bucket with no
 * eligible latency sample was not measured at 0 ms, and drawing it on the baseline would be a claim
 * the data does not support.
 *
 * Tooltips are native SVG `<title>` elements on generously sized transparent hit targets — no
 * JavaScript, no positioning maths, and they are announced by assistive technology.
 */
@Component({
  selector: 'app-analytics-area-chart',
  template: `
    @if (isEmpty()) {
      <p class="chart__empty status-text">{{ emptyMessage() }}</p>
    } @else {
      <figure class="chart" [style.--chart-height.px]="height()">
        <figcaption class="visually-hidden">{{ accessibleSummary() }}</figcaption>

        <div class="chart__frame">
          <div class="chart__y-axis" aria-hidden="true">
            @for (tick of yTicks(); track tick.value) {
              <span class="chart__y-tick" [style.bottom.%]="tick.percent">{{ tick.label }}</span>
            }
          </div>

          <div class="chart__plot">
            <svg
              class="chart__svg"
              [attr.viewBox]="'0 0 ' + plotWidth + ' ' + plotHeight"
              preserveAspectRatio="none"
              role="img"
              [attr.aria-label]="accessibleSummary()"
            >
              <defs>
                <linearGradient [attr.id]="gradientId" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="var(--color-primary)" stop-opacity="0.30" />
                  <stop offset="100%" stop-color="var(--color-primary)" stop-opacity="0" />
                </linearGradient>
              </defs>

              @for (tick of yTicks(); track tick.value) {
                <line
                  class="chart__grid"
                  x1="0"
                  x2="1000"
                  [attr.y1]="tick.y"
                  [attr.y2]="tick.y"
                  vector-effect="non-scaling-stroke"
                />
              }

              @if (filled() && areaPath()) {
                <path class="chart__area" [attr.d]="areaPath()" [attr.fill]="'url(#' + gradientId + ')'" />
              }

              @for (segment of linePaths(); track segment) {
                <path
                  class="chart__line"
                  [class.chart__line--thin]="!filled()"
                  [attr.d]="segment"
                  vector-effect="non-scaling-stroke"
                />
              }

              @for (marker of markers(); track marker.index) {
                <g class="chart__marker">
                  <title>{{ marker.tooltip }}</title>
                  <!-- The visible dot: always shown in line mode (Stitch point radius 4), revealed on
                       hover in area mode (Stitch pointRadius 0 / pointHoverRadius 6). -->
                  <circle
                    class="chart__dot"
                    [class.chart__dot--always]="!filled()"
                    [attr.cx]="marker.x"
                    [attr.cy]="marker.y"
                    r="5"
                    vector-effect="non-scaling-stroke"
                  />
                  <!-- A wide, invisible hit target so the tooltip is reachable without pixel-hunting.
                       Sized in user units, which the horizontal stretch turns into a comfortable band. -->
                  <rect
                    class="chart__hit"
                    [attr.x]="marker.hitX"
                    y="0"
                    [attr.width]="marker.hitWidth"
                    [attr.height]="plotHeight"
                  />
                </g>
              }
            </svg>
          </div>
        </div>

        <div class="chart__x-axis" aria-hidden="true">
          @for (label of xLabels(); track label.index) {
            <span class="chart__x-tick" [style.left.%]="label.percent">{{ label.text }}</span>
          }
        </div>
      </figure>
    }
  `,
  styles: `
    .chart {
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-2);
    }

    .chart__empty {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 8rem;
      margin: 0;
      text-align: center;
    }

    .chart__frame {
      display: flex;
      gap: var(--space-2);
      height: var(--chart-height, 320px);
    }

    .chart__y-axis {
      position: relative;
      flex: none;
      width: 3.25rem;
    }

    .chart__y-tick {
      position: absolute;
      right: 0;
      transform: translateY(50%);
      font-size: var(--text-label);
      color: var(--color-text-faint);
      font-variant-numeric: tabular-nums;
    }

    .chart__plot {
      flex: 1;
      min-width: 0;
    }

    .chart__svg {
      display: block;
      width: 100%;
      height: 100%;
      overflow: visible;
    }

    .chart__grid {
      stroke: var(--color-border);
      stroke-width: 1;
    }

    .chart__line {
      fill: none;
      stroke: var(--color-primary);
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .chart__line--thin {
      stroke-width: 2;
      stroke: var(--color-primary-deep);
    }

    .chart__dot {
      fill: var(--color-primary);
      stroke: #fff;
      stroke-width: 2;
      opacity: 0;
      transition: opacity var(--transition);
    }

    .chart__dot--always {
      opacity: 1;
      fill: var(--color-primary-deep);
    }

    .chart__marker:hover .chart__dot,
    .chart__marker:focus-within .chart__dot {
      opacity: 1;
    }

    .chart__hit {
      fill: transparent;
    }

    .chart__x-axis {
      position: relative;
      height: 1.25rem;
      margin-left: calc(3.25rem + var(--space-2));
    }

    .chart__x-tick {
      position: absolute;
      transform: translateX(-50%);
      white-space: nowrap;
      font-size: var(--text-label);
      color: var(--color-text-faint);
    }

    @media (prefers-reduced-motion: reduce) {
      .chart__dot {
        transition: none;
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AnalyticsAreaChartComponent {
  readonly points = input.required<ChartPoint[]>();

  /** Area (Stitch's large primary chart) versus a plain line (Stitch's smaller trend chart). */
  readonly filled = input(true);

  readonly height = input(320);

  /** What the series counts, used in the tooltip-free accessible summary (e.g. "detections"). */
  readonly seriesLabel = input('value');

  readonly emptyMessage = input('No data for the selected filters.');

  protected readonly plotWidth = PLOT_WIDTH;
  protected readonly plotHeight = PLOT_HEIGHT;

  /** Unique per instance so two charts on the same page cannot share one gradient definition. */
  protected readonly gradientId = `chart-gradient-${Math.random().toString(36).slice(2, 10)}`;

  /** A series with no point, or with no measured point at all, has nothing honest to draw — the card
   *  shows its empty state instead of an axis implying zeros. */
  protected readonly isEmpty = computed(() => {
    const points = this.points();
    return points.length === 0 || points.every((point) => point.value === null);
  });

  private readonly maximum = computed(() => {
    const values = this.points()
      .map((point) => point.value)
      .filter((value): value is number => value !== null);
    return niceAxisMaximum(Math.max(0, ...values));
  });

  protected readonly yTicks = computed(() =>
    axisTicks(this.maximum()).map((value) => ({
      value,
      label: formatTick(value),
      percent: (value / this.maximum()) * 100,
      y: PLOT_HEIGHT - (value / this.maximum()) * PLOT_HEIGHT,
    })),
  );

  private readonly coordinates = computed(() => {
    const points = this.points();
    const maximum = this.maximum();
    const step = points.length > 1 ? PLOT_WIDTH / (points.length - 1) : 0;

    return points.map((point, index) => ({
      index,
      point,
      x: points.length > 1 ? index * step : PLOT_WIDTH / 2,
      y: point.value === null ? null : PLOT_HEIGHT - (point.value / maximum) * PLOT_HEIGHT,
    }));
  });

  /**
   * One path per contiguous run of measured values. Splitting on nulls is what makes an unmeasured
   * bucket read as a gap rather than as a line dropping to the baseline.
   */
  protected readonly linePaths = computed(() => {
    const runs: { x: number; y: number }[][] = [];
    let current: { x: number; y: number }[] = [];

    for (const coordinate of this.coordinates()) {
      if (coordinate.y === null) {
        if (current.length > 0) {
          runs.push(current);
          current = [];
        }
        continue;
      }
      current.push({ x: coordinate.x, y: coordinate.y });
    }

    if (current.length > 0) {
      runs.push(current);
    }

    return runs.map(smoothPath).filter((path) => path.length > 0);
  });

  /** The filled area under the curve — only drawn when the whole series is measured, so a gap is
   *  never quietly closed by a fill that spans it. */
  protected readonly areaPath = computed(() => {
    const paths = this.linePaths();
    const coordinates = this.coordinates().filter((coordinate) => coordinate.y !== null);

    if (paths.length !== 1 || coordinates.length < 2) {
      return '';
    }

    const first = coordinates[0];
    const last = coordinates[coordinates.length - 1];
    return `${paths[0]} L ${last.x} ${PLOT_HEIGHT} L ${first.x} ${PLOT_HEIGHT} Z`;
  });

  protected readonly markers = computed(() => {
    const coordinates = this.coordinates();
    const hitWidth = coordinates.length > 1 ? PLOT_WIDTH / (coordinates.length - 1) : PLOT_WIDTH;

    return coordinates
      .filter((coordinate) => coordinate.y !== null)
      .map((coordinate) => ({
        index: coordinate.index,
        x: coordinate.x,
        y: coordinate.y as number,
        tooltip: `${coordinate.point.label}: ${coordinate.point.tooltip}`,
        hitX: Math.max(0, coordinate.x - hitWidth / 2),
        hitWidth,
      }));
  });

  protected readonly xLabels = computed(() => {
    const points = this.points();
    return labelIndexes(points.length).map((index) => ({
      index,
      text: points[index].label,
      percent: points.length > 1 ? (index / (points.length - 1)) * 100 : 50,
    }));
  });

  /** A one-sentence text equivalent, so the chart is not information available only to sighted users. */
  protected readonly accessibleSummary = computed(() => {
    const measured = this.points().filter((point) => point.value !== null);
    if (measured.length === 0) {
      return this.emptyMessage();
    }

    const first = measured[0];
    const last = measured[measured.length - 1];
    return (
      `${this.seriesLabel()} across ${measured.length} periods, ` +
      `from ${first.tooltip} at ${first.label} to ${last.tooltip} at ${last.label}.`
    );
  });
}

/**
 * A Catmull-Rom spline converted to cubic Béziers, matching the Stitch chart's `tension: 0.4`
 * smoothing. Control points are clamped so the curve cannot overshoot below the baseline and imply a
 * negative count.
 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (points.length === 0) {
    return '';
  }

  if (points.length === 1) {
    // A single measured point still deserves to be visible: a zero-length line with a round cap
    // renders as a dot.
    return `M ${round(points[0].x)} ${round(points[0].y)} L ${round(points[0].x)} ${round(points[0].y)}`;
  }

  const tension = 0.4;
  let path = `M ${round(points[0].x)} ${round(points[0].y)}`;

  for (let i = 0; i < points.length - 1; i++) {
    const previous = points[i - 1] ?? points[i];
    const current = points[i];
    const next = points[i + 1];
    const after = points[i + 2] ?? next;

    const control1 = {
      x: current.x + ((next.x - previous.x) / 6) * tension * 2,
      y: clampY(current.y + ((next.y - previous.y) / 6) * tension * 2),
    };
    const control2 = {
      x: next.x - ((after.x - current.x) / 6) * tension * 2,
      y: clampY(next.y - ((after.y - current.y) / 6) * tension * 2),
    };

    path +=
      ` C ${round(control1.x)} ${round(control1.y)},` +
      ` ${round(control2.x)} ${round(control2.y)},` +
      ` ${round(next.x)} ${round(next.y)}`;
  }

  return path;
}

function clampY(value: number): number {
  return Math.min(PLOT_HEIGHT, Math.max(0, value));
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Compact tick text: 1200 → "1.2k", so a busy axis stays readable in a 3.25rem gutter. */
function formatTick(value: number): string {
  if (value >= 1000) {
    const thousands = value / 1000;
    return `${Number.isInteger(thousands) ? thousands : thousands.toFixed(1)}k`;
  }

  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}
