import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { DonutSlice } from './chart.models';

/** Stitch geometry: a 220×220 canvas at `cutout: '80%'`, i.e. a thin ring rather than a pie. */
const SIZE = 220;
const RADIUS = 92;
const STROKE = 20;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * A dependency-free donut chart (FS-15 §7, IP-17 T-13; Stitch's donut card).
 *
 * The aspect ratio is fixed and square, so unlike the line and bar charts this can be a plain SVG
 * with uniform scaling — no stretched text, no distorted arcs.
 *
 * Segments are drawn as stroked arcs of one circle using `stroke-dasharray`/`stroke-dashoffset`,
 * which needs no arc-path trigonometry and degenerates correctly when a single slice holds 100 % of
 * the total (the case a naive `A` arc-command implementation renders as nothing).
 *
 * The centre value is passed in already formatted. When there is no data, the caller passes `—`
 * rather than a percentage — this component never invents a figure to fill the hole (FS-15 §5.2).
 */
@Component({
  selector: 'app-analytics-donut-chart',
  template: `
    <figure class="donut">
      <figcaption class="visually-hidden">{{ accessibleSummary() }}</figcaption>

      <div class="donut__ring">
        <svg
          [attr.viewBox]="'0 0 ' + size + ' ' + size"
          [attr.width]="size"
          [attr.height]="size"
          role="img"
          [attr.aria-label]="accessibleSummary()"
        >
          <!-- Rotated so the first segment starts at 12 o'clock rather than 3 o'clock. -->
          <g [attr.transform]="'rotate(-90 ' + size / 2 + ' ' + size / 2 + ')'">
            <circle
              class="donut__track"
              [attr.cx]="size / 2"
              [attr.cy]="size / 2"
              [attr.r]="radius"
              [attr.stroke-width]="stroke"
            />
            @for (arc of arcs(); track arc.label) {
              <circle
                class="donut__arc"
                [attr.cx]="size / 2"
                [attr.cy]="size / 2"
                [attr.r]="radius"
                [attr.stroke]="arc.color"
                [attr.stroke-width]="stroke"
                [attr.stroke-dasharray]="arc.dashArray"
                [attr.stroke-dashoffset]="arc.dashOffset"
              >
                <title>{{ arc.label }}: {{ arc.value }}</title>
              </circle>
            }
          </g>
        </svg>

        <div class="donut__centre">
          <span class="donut__value">{{ centreValue() }}</span>
          <span class="donut__caption">{{ centreCaption() }}</span>
        </div>
      </div>

      <ul class="donut__legend">
        @for (arc of arcs(); track arc.label) {
          <li class="donut__legend-row">
            <span class="donut__legend-label">
              <span class="donut__dot" [style.background]="arc.color" aria-hidden="true"></span>
              {{ arc.label }}
            </span>
            <span class="donut__legend-value">{{ arc.value }}</span>
          </li>
        }
      </ul>
    </figure>
  `,
  styles: `
    .donut {
      margin: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-5);
    }

    .donut__ring {
      position: relative;
      align-self: center;
      display: flex;
      align-items: center;
      justify-content: center;
      /* Scales down on a narrow card without ever exceeding the Stitch size. */
      width: min(100%, 220px);
      aspect-ratio: 1;
    }

    .donut__ring svg {
      width: 100%;
      height: 100%;
    }

    .donut__track {
      fill: none;
      stroke: var(--color-surface-subtle);
    }

    .donut__arc {
      fill: none;
      transition: stroke-dasharray var(--transition);
    }

    .donut__centre {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-1);
      pointer-events: none;
      text-align: center;
      padding: 0 var(--space-4);
    }

    .donut__value {
      font-family: var(--font-heading);
      font-size: var(--text-display);
      font-weight: var(--weight-semibold);
      color: var(--color-text);
      line-height: 1.1;
    }

    .donut__caption {
      font-size: var(--text-label);
      font-weight: var(--weight-medium);
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--color-primary-deep);
    }

    .donut__legend {
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .donut__legend-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      font-size: var(--text-sm);
      color: var(--color-text-muted);
    }

    .donut__legend-label {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
    }

    .donut__legend-value {
      font-weight: var(--weight-semibold);
      color: var(--color-text);
      font-variant-numeric: tabular-nums;
    }

    .donut__dot {
      width: 0.625rem;
      height: 0.625rem;
      border-radius: 50%;
      flex: none;
    }

    @media (prefers-reduced-motion: reduce) {
      .donut__arc {
        transition: none;
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AnalyticsDonutChartComponent {
  readonly slices = input.required<DonutSlice[]>();

  /** Already formatted by the caller — `—` when the metric is genuinely unavailable. */
  readonly centreValue = input.required<string>();
  readonly centreCaption = input('');

  protected readonly size = SIZE;
  protected readonly radius = RADIUS;
  protected readonly stroke = STROKE;

  private readonly total = computed(() =>
    this.slices().reduce((sum, slice) => sum + Math.max(0, slice.value), 0),
  );

  /**
   * The arcs, laid out end to end around the ring. With a zero total every arc is empty and only the
   * neutral track shows — an honest "nothing measured" ring rather than an arbitrary full circle.
   */
  protected readonly arcs = computed(() => {
    const total = this.total();
    let consumed = 0;

    return this.slices().map((slice) => {
      const fraction = total > 0 ? Math.max(0, slice.value) / total : 0;
      const length = fraction * CIRCUMFERENCE;
      const offset = -consumed * CIRCUMFERENCE;
      consumed += fraction;

      return {
        label: slice.label,
        value: slice.value,
        color: slice.color,
        dashArray: `${round(length)} ${round(CIRCUMFERENCE - length)}`,
        dashOffset: round(offset),
      };
    });
  });

  protected readonly accessibleSummary = computed(() => {
    const slices = this.slices();
    if (this.total() === 0) {
      return `${this.centreCaption()}: ${this.centreValue()}. No measurements in the selected range.`;
    }

    return (
      `${this.centreCaption()}: ${this.centreValue()}. ` +
      slices.map((slice) => `${slice.label} ${slice.value}`).join(', ') +
      '.'
    );
  });
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
