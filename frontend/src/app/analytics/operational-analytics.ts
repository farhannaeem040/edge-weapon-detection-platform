import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { Branch } from '../branches/branch.models';
import { BranchService } from '../branches/branch.service';
import { AnalyticsBarChartComponent } from '../shared/charts/analytics-bar-chart';
import { AnalyticsAreaChartComponent } from '../shared/charts/analytics-area-chart';
import { AnalyticsDonutChartComponent } from '../shared/charts/analytics-donut-chart';
import { BarDatum, ChartPoint, DonutSlice } from '../shared/charts/chart.models';
import {
  ANALYTICS_DETECTION_TYPE_OPTIONS,
  ANALYTICS_RANGE_OPTIONS,
  AnalyticsBucket,
  AnalyticsDetectionType,
  AnalyticsFilterState,
  AnalyticsRange,
  OperationalAnalytics,
  defaultAnalyticsFilterState,
  toAnalyticsDetectionType,
  toAnalyticsRange,
} from './analytics.models';
import {
  ANALYTICS_BRANCH_ID_QUERY_PARAM,
  ANALYTICS_DETECTION_TYPE_QUERY_PARAM,
  ANALYTICS_RANGE_QUERY_PARAM,
} from './analytics.routes';
import { AnalyticsService } from './analytics.service';

/** The page's mutually exclusive states (FS-15 §7, Phase 18). `branch-missing` is deliberately its
 *  own state rather than an empty result: a deleted Branch must never look like a quiet one. */
type PageState = 'loading' | 'ready' | 'error' | 'branch-missing';

/**
 * The Operational Analytics page (FS-15, IP-17 T-14).
 *
 * **Layout** is adapted from the Stitch "Operational Analytics" screen
 * (project `12701037052481013848`, screen `03a107b0f10b4058adaf3ffcc4a2e3f7`): the header/action
 * composition, the filter card, the 12-column 8/4 + 6/6 bento grid, the card proportions, and the
 * charcoal status footer. Its *branding* is not adopted — the LJMU shell, sidebar and header are
 * untouched, and nothing here carries "Sentinel AI", a persona, an avatar, a search field, or a
 * notification bell.
 *
 * **Every figure on this page is real** (FS-15 §2). Nothing is seeded, defaulted to a plausible
 * constant, or carried over from the mockup. Two Stitch cards could not be honoured as designed and
 * are replaced rather than faked:
 *
 *  - *Detection Accuracy — 98.4 % precision* → **Detection Confidence**. `AlertStatus` has a single
 *    member, so the database holds no confirm/false-positive ground truth (FS-15 §3.4) and no
 *    accuracy figure is computable. The card shows the detector's own mean confidence and its band
 *    distribution, labelled as not human-validated.
 *  - *Avg. Response Time — detection to operator validation* → **Alert Delivery Latency**. No
 *    operator-response timestamp exists; `ReceivedAtUtc − DetectedAtUtc` is real, so it is shown and
 *    named for what it actually measures.
 *
 * **Filter state is the URL's query string**, as on the Alert list (FS-10 §6): every control change
 * navigates, and the fetch is driven by `queryParamMap`, so a refresh or the back button restores
 * exactly what was on screen and there is one source of truth for what is being shown. All four cards
 * are driven by that one state and one request — never a per-card window.
 */
@Component({
  selector: 'app-operational-analytics',
  imports: [AnalyticsAreaChartComponent, AnalyticsBarChartComponent, AnalyticsDonutChartComponent],
  template: `
    <section class="analytics">
      <header class="analytics__header page-header">
        <div class="page-header__titles">
          <span class="breadcrumb">Dashboard / Analytics</span>
          <h2 class="page-header__title">Operational Analytics</h2>
          <p class="analytics__subtitle status-text">
            Performance metrics — analyse security events and delivery performance across Branches.
          </p>
        </div>

        <div class="page-header__actions analytics__actions">
          <button
            class="btn btn--secondary"
            type="button"
            [disabled]="exporting() || state() !== 'ready'"
            (click)="exportCsv()"
          >
            {{ exporting() ? 'Preparing…' : 'Export CSV' }}
          </button>
          <button
            class="btn btn--primary"
            type="button"
            [disabled]="state() !== 'ready'"
            (click)="generateReport()"
          >
            Generate report
          </button>
        </div>
      </header>

      @if (exportError()) {
        <p class="banner banner--error" role="alert">
          The CSV export could not be produced. {{ exportError() }}
        </p>
      }

      <!-- Filter bar (Stitch filter card): one shared state driving every card below. -->
      <div class="analytics__filters card">
        <div class="card__body analytics__filters-body">
          <div class="field analytics__field">
            <label class="field__label" for="analytics-range">Date range</label>
            <!-- Per-option [selected] rather than a value binding on the select: a value write is
                 applied before the loop has created the options, so the control would silently fall
                 back to its first option instead of reflecting the URL. -->
            <select id="analytics-range" name="range" (change)="changeRange($any($event.target).value)">
              @for (option of rangeOptions; track option.value) {
                <option [value]="option.value" [selected]="option.value === filter().range">
                  {{ option.label }}
                </option>
              }
            </select>
          </div>

          <div class="field analytics__field">
            <label class="field__label" for="analytics-branch">Branch</label>
            <!-- Per-option [selected] rather than a value binding: the Branch list resolves
                 asynchronously and can arrive after the filter is read from the URL, so a plain value
                 write can race a select that has no matching option yet (the same reasoning as the
                 Alert list's Branch filter). -->
            <select id="analytics-branch" name="branchId" (change)="changeBranch($any($event.target).value)">
              <option value="" [selected]="!filter().branchId">All Branches</option>
              @for (branch of branches(); track branch.branchId) {
                <option [value]="branch.branchId" [selected]="branch.branchId === filter().branchId">
                  {{ branch.name }}
                </option>
              }
            </select>
          </div>

          <div class="field analytics__field">
            <label class="field__label" for="analytics-detection-type">Detection type</label>
            <select
              id="analytics-detection-type"
              name="detectionType"
              (change)="changeDetectionType($any($event.target).value)"
            >
              <option value="" [selected]="!filter().detectionType">All detections</option>
              @for (option of detectionTypeOptions; track option.value) {
                <option [value]="option.value" [selected]="option.value === filter().detectionType">
                  {{ option.label }}
                </option>
              }
            </select>
          </div>

          <div class="analytics__filters-actions">
            <button
              class="icon-btn"
              type="button"
              aria-label="Refresh analytics"
              title="Refresh analytics"
              [disabled]="state() === 'loading'"
              (click)="refresh()"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
                <path
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      @if (state() === 'branch-missing') {
        <div class="card">
          <p class="card__body banner banner--warning" role="alert">
            That Branch no longer exists. Choose another Branch, or select All Branches.
          </p>
        </div>
      }

      <!-- Printed-only provenance line, so an exported report states exactly what it covers. -->
      <p class="analytics__print-meta">{{ filterDescription() }}</p>

      <div class="analytics__grid">
        <!-- 1. Detections Over Time — Stitch col-span-8 -->
        <section class="analytics__card analytics__card--wide card">
          <div class="card__body">
            <div class="analytics__card-head">
              <div>
                <h3 class="analytics__card-title">Detections over time</h3>
                <p class="analytics__card-sub">
                  Alerts raised from delivered detections, by detection time (UTC,
                  {{ bucketLabel() }}). Quota-suppressed detections are excluded.
                </p>
              </div>
              <span class="analytics__legend">
                <span class="analytics__legend-dot"></span>
                Total detections
              </span>
            </div>

            @switch (state()) {
              @case ('loading') {
                <p class="analytics__pending status-text">
                  <span class="spinner" aria-hidden="true"></span> Loading analytics…
                </p>
              }
              @case ('error') {
                <p class="analytics__pending status-text status-text--error" role="alert">
                  Analytics could not be loaded. Try again.
                </p>
              }
              @default {
                <app-analytics-area-chart
                  [points]="detectionPoints()"
                  [filled]="true"
                  [height]="320"
                  seriesLabel="Detections"
                  emptyMessage="No detections in the selected range."
                />
              }
            }
          </div>
        </section>

        <!-- 2. Detection Confidence — Stitch col-span-4 (replaces the un-computable accuracy donut) -->
        <section class="analytics__card analytics__card--narrow card">
          <div class="card__body">
            <div class="analytics__card-head">
              <div>
                <h3 class="analytics__card-title">Detection confidence</h3>
                <p class="analytics__card-sub">Model confidence distribution — not human-validated.</p>
              </div>
            </div>

            @switch (state()) {
              @case ('loading') {
                <p class="analytics__pending status-text">
                  <span class="spinner" aria-hidden="true"></span> Loading…
                </p>
              }
              @case ('error') {
                <p class="analytics__pending status-text status-text--error" role="alert">
                  Unavailable.
                </p>
              }
              @default {
                <app-analytics-donut-chart
                  [slices]="confidenceSlices()"
                  [centreValue]="meanConfidenceLabel()"
                  centreCaption="Mean confidence"
                />
                <p class="analytics__note">{{ confidenceNote() }}</p>
              }
            }
          </div>
        </section>

        <!-- 3. Alert density by Branch — Stitch col-span-6 -->
        <section class="analytics__card analytics__card--half card">
          <div class="card__body">
            <div class="analytics__card-head">
              <div>
                <h3 class="analytics__card-title">Alert density by Branch</h3>
                <p class="analytics__card-sub">Alert volume per Branch in the selected range.</p>
              </div>
            </div>

            @switch (state()) {
              @case ('loading') {
                <p class="analytics__pending status-text">
                  <span class="spinner" aria-hidden="true"></span> Loading…
                </p>
              }
              @case ('error') {
                <p class="analytics__pending status-text status-text--error" role="alert">
                  Unavailable.
                </p>
              }
              @default {
                <app-analytics-bar-chart
                  [data]="branchBars()"
                  [height]="280"
                  seriesLabel="Alerts"
                  emptyMessage="No Branches to report on yet."
                />
              }
            }
          </div>
        </section>

        <!-- 4. Alert delivery latency — Stitch col-span-6 (replaces the un-computable response time) -->
        <section class="analytics__card analytics__card--half card">
          <div class="card__body">
            <div class="analytics__card-head">
              <div>
                <h3 class="analytics__card-title">Alert delivery latency</h3>
                <p class="analytics__card-sub">
                  Detection on the Jetson → receipt at the Backend ({{ latencyUnitLabel() }}).
                </p>
              </div>
              @if (state() === 'ready' && summary(); as value) {
                <span class="analytics__chip">Median {{ latencyLabel(value.medianDeliveryLatencyMs) }}</span>
              }
            </div>

            @switch (state()) {
              @case ('loading') {
                <p class="analytics__pending status-text">
                  <span class="spinner" aria-hidden="true"></span> Loading…
                </p>
              }
              @case ('error') {
                <p class="analytics__pending status-text status-text--error" role="alert">
                  Unavailable.
                </p>
              }
              @default {
                <app-analytics-area-chart
                  [points]="latencyPoints()"
                  [filled]="false"
                  [height]="280"
                  seriesLabel="Average delivery latency"
                  emptyMessage="No delivery-latency samples in the selected range."
                />
                @if (latencyNote()) {
                  <p class="analytics__note">{{ latencyNote() }}</p>
                }
              }
            }
          </div>
        </section>

        <!-- Status footer — only values the platform can actually supply (FS-15 §5.6). -->
        @if (state() === 'ready' && summary(); as value) {
          <footer class="analytics__status">
            <div class="analytics__status-group">
              <span class="analytics__status-item">
                <span class="analytics__status-dot" aria-hidden="true"></span>
                Analytics up to date
              </span>
              <span class="analytics__status-item">
                Generated <span class="analytics__mono">{{ generatedAtLabel() }}</span>
              </span>
            </div>
            <div class="analytics__status-group">
              <span class="analytics__status-item">Branches in scope: {{ value.branchCount }}</span>
              <span class="analytics__status-item">Cameras: {{ value.cameraCount }}</span>
              <span class="analytics__status-item">Jetson devices: {{ value.deviceCount }}</span>
              <span class="analytics__status-item">
                Median delivery: {{ latencyLabel(value.medianDeliveryLatencyMs) }}
              </span>
            </div>
          </footer>
        }
      </div>
    </section>
  `,
  styles: `
    .analytics__subtitle {
      margin: 0;
    }

    .analytics__actions {
      flex-wrap: wrap;
    }

    .analytics__filters {
      margin-bottom: var(--space-5);
    }

    .analytics__filters-body {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      gap: var(--space-5);
    }

    .analytics__field {
      margin-bottom: 0;
      min-width: 11rem;
      flex: 1 1 11rem;
      max-width: 15rem;
    }

    .analytics__filters-actions {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: var(--space-2);
    }

    /* Stitch bento grid: 12 columns, 8/4 then 6/6, footer full width. */
    .analytics__grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: var(--space-5);
      align-items: start;
    }

    .analytics__card {
      min-width: 0;
    }

    .analytics__card--wide {
      grid-column: span 8;
    }

    .analytics__card--narrow {
      grid-column: span 4;
    }

    .analytics__card--half {
      grid-column: span 6;
    }

    .analytics__card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--space-3);
      margin-bottom: var(--space-5);
    }

    .analytics__card-title {
      font-size: var(--text-heading);
    }

    .analytics__card-sub {
      margin: var(--space-1) 0 0;
      font-size: var(--text-label);
      color: var(--color-text-faint);
    }

    .analytics__legend {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      font-size: var(--text-label);
      color: var(--color-text-muted);
      white-space: nowrap;
    }

    .analytics__legend-dot {
      width: 0.75rem;
      height: 0.75rem;
      border-radius: 50%;
      background: var(--color-primary);
    }

    .analytics__chip {
      padding: 0.15rem 0.7rem;
      border-radius: var(--radius-pill);
      background: var(--color-primary-tint);
      color: var(--color-primary-deep);
      font-size: var(--text-label);
      font-weight: var(--weight-semibold);
      white-space: nowrap;
    }

    .analytics__pending {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
      min-height: 10rem;
      margin: 0;
      text-align: center;
    }

    .analytics__note {
      margin: var(--space-4) 0 0;
      font-size: var(--text-label);
      line-height: 1.45;
      color: var(--color-text-faint);
    }

    /* Stitch's charcoal technical footer, restricted to values this platform really has. */
    .analytics__status {
      grid-column: span 12;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3) var(--space-5);
      padding: var(--space-4);
      border-radius: var(--radius-md);
      background: var(--color-charcoal);
      color: var(--color-text-on-dark-muted);
      font-size: var(--text-label);
    }

    .analytics__status-group {
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-4);
    }

    .analytics__status-item {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
    }

    .analytics__status-dot {
      width: 0.5rem;
      height: 0.5rem;
      border-radius: 50%;
      background: var(--color-secondary);
    }

    .analytics__mono {
      font-family: var(--font-mono);
      color: var(--color-text-on-dark);
    }

    .analytics__print-meta {
      display: none;
    }

    /* Stitch collapses its bento grid below lg (1024px); so does this one. */
    @media (max-width: 1024px) {
      .analytics__card--wide,
      .analytics__card--narrow,
      .analytics__card--half {
        grid-column: span 12;
      }
    }

    @media (max-width: 600px) {
      .analytics__field {
        max-width: none;
      }

      .analytics__filters-actions {
        margin-left: 0;
      }
    }

    /* "Generate report": the same real metrics, laid out for Print → Save as PDF. Interactive
       controls are suppressed because they mean nothing on paper, and the provenance line is
       revealed so the printout states its own filters and generation time. */
    @media print {
      .analytics__filters,
      .analytics__actions {
        display: none;
      }

      .analytics__print-meta {
        display: block;
        margin: 0 0 var(--space-5);
        font-size: var(--text-sm);
        color: var(--color-text-muted);
      }

      .analytics__grid {
        display: block;
      }

      .analytics__card {
        margin-bottom: var(--space-5);
        break-inside: avoid;
        box-shadow: none;
      }

      .analytics__status {
        background: none;
        color: var(--color-text-muted);
        border: 1px solid var(--color-border);
      }

      .analytics__mono {
        color: var(--color-text);
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OperationalAnalyticsComponent implements OnInit {
  private readonly analyticsService = inject(AnalyticsService);
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly rangeOptions = ANALYTICS_RANGE_OPTIONS;
  protected readonly detectionTypeOptions = ANALYTICS_DETECTION_TYPE_OPTIONS;

  /**
   * Every Branch, for the filter dropdown only — read from the real Admin Branch API, never
   * hard-coded and never taken from the Stitch mockup's facility names. A failure to load this list
   * is not a page-level error: analytics still works across All Branches, so the dropdown is simply
   * empty-but-present rather than blocking the page (the same posture as the Alert list).
   */
  protected readonly branches = signal<Branch[]>([]);

  protected readonly filter = signal<AnalyticsFilterState>(defaultAnalyticsFilterState());
  protected readonly state = signal<PageState>('loading');
  protected readonly analytics = signal<OperationalAnalytics | null>(null);
  protected readonly exporting = signal(false);
  protected readonly exportError = signal<string | null>(null);

  protected readonly summary = computed(() => this.analytics()?.summary ?? null);

  ngOnInit(): void {
    this.branchService.list().subscribe({
      next: (branches) => this.branches.set(branches),
      error: () => {},
    });

    // The URL is the single source of truth: this subscription is the only thing that fetches, so a
    // control change and a browser Back both take exactly the same path, and neither can fire a
    // second request for a state that is already on screen.
    this.route.queryParamMap.subscribe((params) => {
      this.filter.set({
        range: toAnalyticsRange(params.get(ANALYTICS_RANGE_QUERY_PARAM)),
        branchId: params.get(ANALYTICS_BRANCH_ID_QUERY_PARAM) ?? undefined,
        detectionType: toAnalyticsDetectionType(params.get(ANALYTICS_DETECTION_TYPE_QUERY_PARAM)),
      });
      this.fetch();
    });
  }

  // ------------------------------------------------------------------ Filter actions

  protected changeRange(value: string): void {
    this.navigateTo({ ...this.filter(), range: toAnalyticsRange(value) });
  }

  protected changeBranch(value: string): void {
    this.navigateTo({ ...this.filter(), branchId: value || undefined });
  }

  protected changeDetectionType(value: string): void {
    this.navigateTo({ ...this.filter(), detectionType: toAnalyticsDetectionType(value) });
  }

  /** An explicit re-fetch of the current filter state. It deliberately does not navigate: the URL is
   *  already correct, and navigating to identical params would emit nothing. */
  protected refresh(): void {
    this.fetch();
  }

  private navigateTo(filter: AnalyticsFilterState): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        [ANALYTICS_RANGE_QUERY_PARAM]:
          filter.range === defaultAnalyticsFilterState().range ? null : filter.range,
        [ANALYTICS_BRANCH_ID_QUERY_PARAM]: filter.branchId ?? null,
        [ANALYTICS_DETECTION_TYPE_QUERY_PARAM]: filter.detectionType ?? null,
      },
    });
  }

  private fetch(): void {
    this.state.set('loading');
    this.exportError.set(null);

    this.analyticsService.getOperational(this.filter()).subscribe({
      next: (result) => {
        this.analytics.set(result);
        // A null result is the Backend's documented "that Branch does not exist" answer — its own
        // state, never an empty dashboard that would read as a real but quiet Branch.
        this.state.set(result === null ? 'branch-missing' : 'ready');
      },
      // A 401 has already been handled globally by the session-expiry interceptor before this runs.
      error: () => {
        this.analytics.set(null);
        this.state.set('error');
      },
    });
  }

  // ------------------------------------------------------------------ Export / report

  protected exportCsv(): void {
    if (this.exporting()) {
      return;
    }

    this.exporting.set(true);
    this.exportError.set(null);

    this.analyticsService.downloadCsv(this.filter()).subscribe({
      next: ({ blob, fileName }) => {
        this.exporting.set(false);
        saveBlob(blob, fileName);
      },
      error: () => {
        this.exporting.set(false);
        this.exportError.set('Try narrowing the date range, Branch, or detection type.');
      },
    });
  }

  /**
   * FS-15 §7 / IP-17 §1.9: the report is a print-optimised rendering of the same real metrics, driven
   * by this component's own `@media print` rules. No PDF library and no server-side rendering
   * subsystem is introduced for a mockup button — and the button is never a dead control.
   */
  protected generateReport(): void {
    window.print();
  }

  // ------------------------------------------------------------------ Chart projections

  protected readonly bucketLabel = computed(() => {
    switch (this.analytics()?.filters.bucket) {
      case 'hour':
        return 'hourly';
      case 'week':
        return 'weekly';
      default:
        return 'daily';
    }
  });

  protected readonly detectionPoints = computed<ChartPoint[]>(() => {
    const data = this.analytics();
    if (!data) {
      return [];
    }

    return data.detectionsOverTime.map((bucket) => ({
      label: formatBucket(bucket.periodStartUtc, data.filters.bucket),
      value: bucket.count,
      tooltip: `${bucket.count} ${bucket.count === 1 ? 'detection' : 'detections'}`,
    }));
  });

  protected readonly branchBars = computed<BarDatum[]>(() =>
    (this.analytics()?.detectionsByBranch ?? []).map((row) => ({
      label: row.branchName,
      value: row.count,
      tooltip: `${row.branchName}: ${row.count} ${row.count === 1 ? 'alert' : 'alerts'}`,
    })),
  );

  /** Milliseconds below one second, seconds above it — chosen once for the whole series so the axis,
   *  the tooltips and the card subtitle never disagree about the unit (FS-15 §5.4). */
  private readonly latencyInSeconds = computed(() => {
    const values = (this.analytics()?.latencyOverTime ?? [])
      .map((bucket) => bucket.averageMs)
      .filter((value): value is number => value !== null && value !== undefined);

    return values.length > 0 && Math.max(...values) >= 1000;
  });

  protected readonly latencyUnitLabel = computed(() => (this.latencyInSeconds() ? 'seconds' : 'ms'));

  protected readonly latencyPoints = computed<ChartPoint[]>(() => {
    const data = this.analytics();
    if (!data) {
      return [];
    }

    const inSeconds = this.latencyInSeconds();

    return data.latencyOverTime.map((bucket) => {
      const raw = bucket.averageMs ?? null;
      return {
        label: formatBucket(bucket.periodStartUtc, data.filters.bucket),
        // Null stays null: an unmeasured bucket is a gap, never 0 ms.
        value: raw === null ? null : inSeconds ? round(raw / 1000, 2) : Math.round(raw),
        tooltip: raw === null ? 'No samples' : formatLatency(raw),
      };
    });
  });

  protected readonly confidenceSlices = computed<DonutSlice[]>(() => {
    const summary = this.summary();
    if (!summary) {
      return [];
    }

    const high = Math.round(summary.highConfidenceThreshold * 100);
    const medium = Math.round(summary.mediumConfidenceThreshold * 100);

    return [
      { label: `High (≥ ${high}%)`, value: summary.confidenceHigh, color: 'var(--color-primary)' },
      {
        label: `Medium (${medium}–${high}%)`,
        value: summary.confidenceMedium,
        color: 'var(--color-secondary)',
      },
      { label: `Low (< ${medium}%)`, value: summary.confidenceLow, color: 'var(--color-border-strong)' },
    ];
  });

  /** An em dash, never `0%`, when there is nothing to average (FS-15 §7, Phase 18). */
  protected readonly meanConfidenceLabel = computed(() => {
    const mean = this.summary()?.meanConfidence;
    return mean === null || mean === undefined ? '—' : `${(mean * 100).toFixed(1)}%`;
  });

  protected readonly confidenceNote = computed(() => {
    const summary = this.summary();

    if (!summary || summary.totalDetections === 0) {
      return 'No detections in the selected range, so there is no confidence to summarise.';
    }

    // Stated on every render, not only when something looks wrong: the number above is the detector's
    // own score, and must never be read as an accuracy or a false-positive rate (FS-15 §5.2).
    return summary.validationDataAvailable
      ? 'Confidence reported by the detector across the selected detections.'
      : 'Detector confidence across ' +
          `${summary.totalDetections} ${summary.totalDetections === 1 ? 'detection' : 'detections'}. ` +
          'This is not an accuracy or false-positive rate: the platform has no operator review ' +
          'workflow yet, so no validated ground truth exists.';
  });

  protected readonly latencyNote = computed(() => {
    const summary = this.summary();
    if (!summary) {
      return '';
    }

    // Silent when there is nothing to summarise: the chart's own empty state already says so, and
    // repeating it directly underneath reads as two separate findings rather than one.
    if (summary.latencySampleCount === 0) {
      return '';
    }

    const parts = [
      `Mean ${this.latencyLabel(summary.averageDeliveryLatencyMs)}`,
      `median ${this.latencyLabel(summary.medianDeliveryLatencyMs)}`,
      `max ${this.latencyLabel(summary.maxDeliveryLatencyMs)}`,
      `across ${summary.latencySampleCount} ${summary.latencySampleCount === 1 ? 'sample' : 'samples'}`,
    ];

    let note = `${parts.join(', ')}.`;

    // Exclusions are reported, never silently dropped (FS-15 §5.4).
    if (summary.latencySamplesExcluded > 0) {
      note +=
        ` ${summary.latencySamplesExcluded} ` +
        `${summary.latencySamplesExcluded === 1 ? 'detection was' : 'detections were'} excluded from ` +
        'this metric (clock skew, or an offline backlog replayed more than a day after detection).';
    }

    return note;
  });

  protected readonly generatedAtLabel = computed(() => {
    const generatedAtUtc = this.summary()?.generatedAtUtc;
    return generatedAtUtc ? `${formatUtc(generatedAtUtc)} UTC` : '—';
  });

  /** The printed provenance line — what this report covers and when it was produced. */
  protected readonly filterDescription = computed(() => {
    const data = this.analytics();
    if (!data) {
      return '';
    }

    const range =
      ANALYTICS_RANGE_OPTIONS.find((option) => option.value === data.filters.range)?.label ??
      'Custom range';
    const branch = data.filters.branchName ?? 'All Branches';
    const detectionType =
      ANALYTICS_DETECTION_TYPE_OPTIONS.find((option) => option.value === data.filters.detectionType)
        ?.label ?? 'All detections';

    return (
      `${range} · ${branch} · ${detectionType} · ` +
      `${formatUtc(data.filters.fromUtc)} to ${formatUtc(data.filters.toUtc)} UTC · ` +
      `generated ${this.generatedAtLabel()}`
    );
  });

  /** Shared by the chip, the footer and the note, so one unit rule governs every latency on the page. */
  protected latencyLabel(milliseconds: number | null | undefined): string {
    return milliseconds === null || milliseconds === undefined ? '—' : formatLatency(milliseconds);
  }
}

/** Milliseconds below one second; seconds to one decimal at or above it (FS-15 §5.4 units rule). */
function formatLatency(milliseconds: number): string {
  return milliseconds < 1000
    ? `${Math.round(milliseconds)} ms`
    : `${(milliseconds / 1000).toFixed(1)} s`;
}

/**
 * Bucket labels are rendered in **UTC**, matching the Backend's UTC bucketing (FS-15 §4.1) — showing
 * a UTC-anchored bucket under a local-time label would misstate which period each point covers.
 */
function formatBucket(isoUtc: string, bucket: AnalyticsBucket): string {
  const date = new Date(isoUtc);

  if (bucket === 'hour') {
    return new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
      hour12: false,
    }).format(date);
  }

  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  }).format(date);
}

function formatUtc(isoUtc: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    hour12: false,
  }).format(new Date(isoUtc));
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

/**
 * Hands the Backend-generated CSV to the browser's download mechanism. The bytes come entirely from
 * the Backend — nothing is reconstructed here — and the object URL is revoked immediately so the blob
 * is not retained for the lifetime of the page.
 */
function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
