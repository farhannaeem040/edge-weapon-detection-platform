import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { BarDatum, axisTicks, niceAxisMaximum } from './chart.models';

/**
 * A dependency-free bar chart (FS-15 §7, IP-17 T-13; Stitch "Alert Density by Site" card, adapted to
 * Branch).
 *
 * Built from HTML and CSS rather than SVG, deliberately: bars are rectangles with rounded tops and
 * text labels, all of which CSS does natively and crisply at any width — whereas a horizontally
 * stretched SVG would distort both the bar corners and the labels.
 *
 * Handles the three shapes FS-15 §7 requires without breaking: no Branches at all (empty state), a
 * single Branch (one bar, not one bar stretched across the card), and many Branches (bars shrink to a
 * floor, and the row scrolls horizontally rather than overflowing the card).
 *
 * A zero-count Branch renders a visible baseline stub with its label intact, so "this Branch is
 * quiet" reads differently from "this Branch is missing" (FS-15 §5.3).
 */
@Component({
  selector: 'app-analytics-bar-chart',
  template: `
    @if (data().length === 0) {
      <p class="bars__empty status-text">{{ emptyMessage() }}</p>
    } @else {
      <figure class="bars" [style.--bars-height.px]="height()">
        <figcaption class="visually-hidden">{{ accessibleSummary() }}</figcaption>

        <div class="bars__frame">
          <div class="bars__y-axis" aria-hidden="true">
            @for (tick of yTicks(); track tick.value) {
              <span class="bars__y-tick" [style.bottom.%]="tick.percent">{{ tick.label }}</span>
            }
          </div>

          <div class="bars__plot">
            @for (tick of yTicks(); track tick.value) {
              <span class="bars__grid" [style.bottom.%]="tick.percent" aria-hidden="true"></span>
            }

            <ol class="bars__list">
              @for (bar of bars(); track bar.label) {
                <li class="bars__item">
                  <span class="bars__column" [title]="bar.tooltip">
                    <span
                      class="bars__fill"
                      [class.bars__fill--zero]="bar.value === 0"
                      [style.height.%]="bar.percent"
                    ></span>
                  </span>
                  <span class="bars__label" [title]="bar.label">{{ bar.label }}</span>
                </li>
              }
            </ol>
          </div>
        </div>
      </figure>
    }
  `,
  styles: `
    .bars {
      margin: 0;
    }

    .bars__empty {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 8rem;
      margin: 0;
      text-align: center;
    }

    .bars__frame {
      display: flex;
      gap: var(--space-2);
    }

    .bars__y-axis {
      position: relative;
      flex: none;
      width: 2.75rem;
      /* Aligned to the plot area only, so the ticks are not pushed out of register by the label row. */
      height: var(--bars-height, 280px);
    }

    .bars__y-tick {
      position: absolute;
      right: 0;
      transform: translateY(50%);
      font-size: var(--text-label);
      color: var(--color-text-faint);
      font-variant-numeric: tabular-nums;
    }

    .bars__plot {
      position: relative;
      flex: 1;
      min-width: 0;
      overflow-x: auto;
    }

    .bars__grid {
      position: absolute;
      left: 0;
      right: 0;
      height: 1px;
      background: var(--color-border);
      opacity: 0.6;
    }

    .bars__list {
      position: relative;
      display: flex;
      align-items: flex-end;
      justify-content: space-around;
      gap: var(--space-3);
      height: var(--bars-height, 280px);
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .bars__item {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--space-2);
      /* A floor keeps many Branches legible; the plot scrolls rather than crushing them. */
      min-width: 3.5rem;
      max-width: 6rem;
      flex: 1 1 0;
      height: 100%;
      justify-content: flex-end;
    }

    .bars__column {
      display: flex;
      align-items: flex-end;
      justify-content: center;
      width: 100%;
      /* Reserve the label row (two lines) out of the total height. */
      height: calc(100% - 2.75rem);
    }

    .bars__fill {
      display: block;
      /* Stitch: barThickness 28, borderRadius 4 — capped by the column so a lone Branch does not
         become a single bar stretched across the whole card. */
      width: 28px;
      max-width: 100%;
      min-height: 2px;
      background: var(--color-primary);
      border-radius: var(--radius-sm) var(--radius-sm) 0 0;
    }

    .bars__fill--zero {
      background: var(--color-border-strong);
    }

    .bars__label {
      display: -webkit-box;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      max-width: 100%;
      text-align: center;
      font-size: var(--text-label);
      line-height: 1.25;
      color: var(--color-text-muted);
      overflow-wrap: anywhere;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AnalyticsBarChartComponent {
  readonly data = input.required<BarDatum[]>();
  readonly height = input(280);
  readonly emptyMessage = input('No data for the selected filters.');

  /** What the bars count, used in the accessible summary (e.g. "alerts"). */
  readonly seriesLabel = input('value');

  private readonly maximum = computed(() =>
    niceAxisMaximum(Math.max(0, ...this.data().map((datum) => datum.value))),
  );

  protected readonly yTicks = computed(() =>
    axisTicks(this.maximum()).map((value) => ({
      value,
      label: Number.isInteger(value) ? String(value) : value.toFixed(1),
      percent: (value / this.maximum()) * 100,
    })),
  );

  protected readonly bars = computed(() =>
    this.data().map((datum) => ({
      ...datum,
      // A zero-count Branch still gets its 2px minimum stub (see `.bars__fill`) so its label is
      // anchored to something visible rather than floating over an empty column.
      percent: (datum.value / this.maximum()) * 100,
    })),
  );

  protected readonly accessibleSummary = computed(() => {
    const data = this.data();
    if (data.length === 0) {
      return this.emptyMessage();
    }

    return `${this.seriesLabel()} by Branch: ${data
      .map((datum) => `${datum.label} ${datum.value}`)
      .join(', ')}.`;
  });
}
