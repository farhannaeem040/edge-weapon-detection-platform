import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, interval, startWith, switchMap } from 'rxjs';

import { Branch } from '../branches/branch.models';
import { BranchService } from '../branches/branch.service';
import { ALERTS_ROUTE } from '../alerts/alert.routes';
import { BRANCH_ID_QUERY_PARAM } from './dashboard.routes';
import { DashboardSummary } from './dashboard.models';
import { DashboardService } from './dashboard.service';

/**
 * How often the summary is silently re-fetched while this view is open (FS-10 §11). One shared
 * stream drives it — never a second concurrent poll — and it stops the moment the component is
 * destroyed (`takeUntilDestroyed`), so navigating away leaves nothing running in the background.
 */
const POLL_INTERVAL_MS = 15_000;

/**
 * The Admin Dashboard's operational summary (FS-10 §6 "Dashboard content", AC per IP-12 T-200–T-203),
 * extended by manual-review Correction 3: the dashboard is now **explicitly** Branch-scoped, never an
 * implicit "first Branch" pick.
 *
 * `branchId` lives in the URL (`?branchId=<id>`), the single source of truth for which Branch's data
 * is on screen — a refresh or the back/forward button restores exactly the same view. On load, the
 * full Branch list is fetched once (to populate the selector and validate the URL's `branchId`, never
 * to aggregate data across Branches): with no `branchId` supplied and exactly one Branch, it is
 * auto-selected into the URL; with more than one Branch and none selected, an explicit
 * selection-required state is shown — this view never silently guesses which Branch to display.
 * Changing the selector re-navigates, which is what actually changes which Branch's summary loads
 * (`selectedBranchId` below is derived from the URL, not written directly).
 *
 * States are modelled explicitly, the same discipline `BranchListComponent` uses: `branchesLoading`
 * (first fetch of the Branch list), `branchesFailed` (that fetch failed), `noBranches` (the list is
 * empty), `selectionRequired` (more than one Branch, none chosen), `branchNotFound` (the URL's
 * `branchId` matches no known Branch — including one deleted after the URL was bookmarked), `loading`
 * (first fetch of the selected Branch's summary), `failed` (that fetch failed before any success), and
 * only then `loaded`. A background poll tick never flips the view back to `loading`: only a genuine
 * Branch switch does that — a slow or failed refresh cannot make already-visible data blink away, and
 * the previous summary and its `lastRefreshedAt` timestamp simply stay on screen (FS-10 §12).
 */
@Component({
  selector: 'app-dashboard-summary',
  imports: [DatePipe, RouterLink, FormsModule],
  template: `
    <section class="dashboard">
      <header class="dashboard__header page-header">
        <div class="page-header__titles">
          <span class="breadcrumb">Dashboard</span>
          <h2 class="page-header__title">Operations overview</h2>
        </div>
        <div class="page-header__actions">
          @if (branches().length > 0) {
            <label class="dashboard__branch-select-label">
              Branch:
              <select
                class="dashboard__branch-select"
                [ngModel]="selectedBranchId()"
                (ngModelChange)="onBranchSelected($event)"
                name="branch"
              >
                @if (selectedBranchId() === null) {
                  <option [ngValue]="null" disabled>Select a Branch…</option>
                }
                @for (branch of branches(); track branch.branchId) {
                  <option [ngValue]="branch.branchId">{{ branch.name }}</option>
                }
              </select>
            </label>
          }

          @if (lastRefreshedAt(); as refreshed) {
            <span class="dashboard__refreshed status-text">Updated {{ refreshed | date: 'HH:mm:ss' }}</span>
          }
          <button
            class="btn btn--secondary"
            type="button"
            [disabled]="loading() || selectedBranchId() === null"
            (click)="refresh()"
          >
            Refresh
          </button>
        </div>
      </header>

      @if (branchesLoading()) {
        <div class="card">
          <p class="dashboard__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading branches…
          </p>
        </div>
      } @else if (branchesFailed()) {
        <div class="card">
          <p
            class="dashboard__status dashboard__status--error card__body banner banner--error"
            role="alert"
          >
            The Branch list could not be loaded. Try again.
          </p>
        </div>
      } @else if (noBranches()) {
        <div class="card">
          <div class="dashboard__empty card__body empty-state">
            <p class="dashboard__status">No Branch is configured yet.</p>
          </div>
        </div>
      } @else if (selectionRequired()) {
        <div class="card">
          <div class="dashboard__empty card__body empty-state">
            <p class="dashboard__status">Select a Branch above to view its dashboard.</p>
          </div>
        </div>
      } @else if (branchNotFound()) {
        <div class="card">
          <p class="dashboard__status card__body banner banner--info" role="alert">
            That Branch was not found. It may have been removed.
          </p>
        </div>
      } @else if (loading()) {
        <div class="card">
          <p class="dashboard__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading dashboard…
          </p>
        </div>
      } @else if (failed()) {
        <!-- Deliberately generic: the Backend's own error text is never surfaced (FS-02 §11 precedent). -->
        <div class="card">
          <p
            class="dashboard__status dashboard__status--error card__body banner banner--error"
            role="alert"
          >
            The dashboard could not be loaded. Try again.
          </p>
        </div>
      } @else if (summary(); as data) {
        <div class="dashboard__grid">
          <div class="card dashboard__quota-card">
            <div class="card__header">
              <!-- The actual Branch name from the Backend, never a hard-coded placeholder — this
                   scopes the whole card to one specific, explicitly-selected Branch. -->
              <h3>{{ data.branch.name }} daily Alert quota</h3>
            </div>
            <div class="card__body">
              <div class="dashboard__quota-headline">
                <span class="dashboard__quota-count">
                  {{ data.alerts.today }} / {{ data.alerts.configuredMaximum }}
                </span>
                <span class="dashboard__quota-label">Alerts used today</span>
              </div>

              <!-- Explicit scope statement: this is never a system-wide total — every Device and every
                   Camera belonging to this one Branch shares it, and another Branch's quota is
                   entirely independent (FS-09 §4, AlertSyncService). -->
              <p class="dashboard__quota-scope status-text">
                Shared across all devices and cameras in this Branch
              </p>

              <div
                class="progress"
                role="progressbar"
                [attr.aria-valuenow]="data.alerts.today"
                [attr.aria-valuemin]="0"
                [attr.aria-valuemax]="data.alerts.configuredMaximum"
              >
                <div
                  class="progress__bar"
                  [class.progress__bar--full]="data.alerts.remaining === 0"
                  [style.width.%]="quotaUsedPercent(data)"
                ></div>
              </div>

              @if (data.alerts.remaining > 0) {
                <p class="dashboard__quota-remaining status-text">
                  {{ data.alerts.remaining }} remaining · {{ data.suppressions.total }} additional
                  detections suppressed
                </p>
              } @else {
                <p class="dashboard__quota-remaining banner banner--warning" role="status">
                  Branch daily quota reached<br />
                  Further detections for this Branch are being suppressed
                </p>
              }

              <p class="dashboard__quota-reset status-text">
                Resets at {{ data.branch.nextQuotaResetAtUtc | date: 'HH:mm' }}
                {{ data.branch.timeZoneId ?? 'UTC' }}
              </p>
            </div>
          </div>

          <div class="stat-grid dashboard__tiles">
            <div class="card">
              <div class="card__body stat-tile">
                <span class="stat-tile__label">Suppressed today</span>
                <span class="stat-tile__value">{{ data.suppressions.total }}</span>
                <span class="stat-tile__meta">
                  {{ data.suppressions.gun }} gun · {{ data.suppressions.knife }} knife
                </span>
              </div>
            </div>

            <div class="card">
              <div class="card__body stat-tile">
                <span class="stat-tile__label">Devices</span>
                <span class="stat-tile__value">{{ data.system.deviceCount }}</span>
                <span class="stat-tile__meta">{{ data.system.cameraCount }} cameras</span>
              </div>
            </div>

            <div class="card">
              <div class="card__body stat-tile">
                <span class="stat-tile__label">Latest Alert</span>
                @if (data.alerts.latestAlertAtUtc; as latest) {
                  <span class="stat-tile__value stat-tile__value--compact">
                    {{ latest | date: 'HH:mm:ss' }}
                  </span>
                } @else {
                  <span class="stat-tile__value stat-tile__value--compact">None yet today</span>
                }
              </div>
            </div>
          </div>

          <!-- Carries the selected Branch's id forward, so the Alerts list opens already filtered to
               it rather than dropping the Branch context the Admin just chose here. -->
          <a class="btn btn--secondary dashboard__view-alerts" [routerLink]="alertsRoute" [queryParams]="alertsQueryParams()">
            View Alerts for this Branch
          </a>
        </div>
      }
    </section>
  `,
  styles: `
    .dashboard__branch-select-label {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      font-size: var(--text-sm);
      color: var(--color-text-muted);
    }

    .dashboard__branch-select {
      min-width: 12rem;
    }

    .dashboard__grid {
      display: flex;
      flex-direction: column;
      gap: var(--space-5);
    }

    .dashboard__quota-headline {
      display: flex;
      align-items: baseline;
      gap: var(--space-2);
      margin-bottom: var(--space-2);
    }

    .dashboard__quota-count {
      font-family: var(--font-heading);
      font-size: var(--text-display);
      font-weight: var(--weight-semibold);
      color: var(--color-text);
    }

    .dashboard__quota-label {
      font-size: var(--text-sm);
      color: var(--color-text-muted);
    }

    .dashboard__quota-scope {
      margin: 0 0 var(--space-3);
    }

    .dashboard__quota-remaining {
      margin-top: var(--space-3);
    }

    .dashboard__quota-reset {
      margin-top: var(--space-2);
    }

    .stat-tile__value--compact {
      font-size: var(--text-heading);
    }

    .dashboard__view-alerts {
      align-self: flex-start;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardSummaryComponent {
  private readonly dashboardService = inject(DashboardService);
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly alertsRoute = ALERTS_ROUTE;

  protected readonly branches = signal<Branch[]>([]);
  protected readonly branchesLoading = signal(true);
  protected readonly branchesFailed = signal(false);

  protected readonly noBranches = signal(false);
  protected readonly selectionRequired = signal(false);
  protected readonly branchNotFound = signal(false);

  /** The Branch id currently on screen — derived from the URL, never written to directly by a click. */
  protected readonly selectedBranchId = signal<string | null>(null);

  protected readonly summary = signal<DashboardSummary | null>(null);
  protected readonly loading = signal(true);
  protected readonly failed = signal(false);
  protected readonly lastRefreshedAt = signal<Date | null>(null);

  protected readonly alertsQueryParams = () =>
    this.selectedBranchId() ? { [BRANCH_ID_QUERY_PARAM]: this.selectedBranchId() } : {};

  constructor() {
    this.branchService.list().subscribe({
      next: (branches) => {
        this.branches.set(branches);
        this.branchesLoading.set(false);
        this.resolveSelection(branches, this.route.snapshot.queryParamMap.get(BRANCH_ID_QUERY_PARAM));
      },
      error: () => {
        this.branchesLoading.set(false);
        this.branchesFailed.set(true);
      },
    });

    // Reacts to the back/forward button and to the selector's own navigation (`onBranchSelected`) —
    // both change the URL, and this is the one place that turns the new URL into the resolved
    // selection state. Ignored until the Branch list itself has loaded, since resolving requires it.
    this.route.queryParamMap.subscribe((params) => {
      if (this.branchesLoading()) {
        return;
      }
      this.resolveSelection(this.branches(), params.get(BRANCH_ID_QUERY_PARAM));
    });

    // Polls whichever Branch is currently selected; switches cleanly to a new poll stream (cancelling
    // the previous one) the moment `selectedBranchId` changes, and polls nothing while it is null.
    toObservable(this.selectedBranchId)
      .pipe(
        switchMap((branchId) => {
          if (branchId === null) {
            return EMPTY;
          }
          return interval(POLL_INTERVAL_MS).pipe(
            startWith(0),
            switchMap(() => this.dashboardService.getSummary(branchId)),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe({
        next: (summary) => this.onSummary(summary),
        // A 401 has already been handled globally (T-25) before this runs. Anything else settles
        // into the failure state, but only ever on the first load — see `onError`.
        error: () => this.onError(),
      });
  }

  /** Navigates so the URL reflects the newly chosen Branch — the actual state change happens there. */
  protected onBranchSelected(branchId: string | null): void {
    if (branchId === null) {
      return;
    }

    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { [BRANCH_ID_QUERY_PARAM]: branchId },
    });
  }

  /** Re-fetches immediately, independent of the poll timer, without resetting it (FS-10 §11). */
  protected refresh(): void {
    const branchId = this.selectedBranchId();
    if (this.loading() || branchId === null) {
      return;
    }

    this.dashboardService.getSummary(branchId).subscribe({
      next: (summary) => this.onSummary(summary),
      error: () => this.onError(),
    });
  }

  protected quotaUsedPercent(data: DashboardSummary): number {
    if (data.alerts.configuredMaximum <= 0) {
      return 0;
    }

    return Math.min(100, (data.alerts.today / data.alerts.configuredMaximum) * 100);
  }

  /**
   * Turns the current Branch list and the URL's `branchId` (or its absence) into exactly one of:
   * a validated selection, a not-found state, a selection-required state, or (with no Branches at
   * all) the pre-existing empty state. Never picks a Branch on its own when more than one exists.
   */
  private resolveSelection(branches: Branch[], requestedBranchId: string | null): void {
    this.noBranches.set(false);
    this.selectionRequired.set(false);
    this.branchNotFound.set(false);

    if (branches.length === 0) {
      this.noBranches.set(true);
      this.selectedBranchId.set(null);
      return;
    }

    if (requestedBranchId !== null) {
      const match = branches.some((branch) => branch.branchId === requestedBranchId);
      if (!match) {
        this.branchNotFound.set(true);
        this.selectedBranchId.set(null);
        return;
      }

      this.selectToBranch(requestedBranchId);
      return;
    }

    if (branches.length === 1) {
      // Exactly one Branch and none selected: auto-select it into the URL (replacing the current
      // history entry, not adding one) — this re-fires the queryParamMap subscription above with the
      // id now present, which resolves the selection on the next call.
      void this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { [BRANCH_ID_QUERY_PARAM]: branches[0].branchId },
        replaceUrl: true,
      });
      return;
    }

    // More than one Branch and none chosen: this view never guesses.
    this.selectionRequired.set(true);
    this.selectedBranchId.set(null);
  }

  private selectToBranch(branchId: string): void {
    if (this.selectedBranchId() === branchId) {
      return;
    }

    // A genuine Branch switch resets to a first-load presentation for that Branch — never carries
    // over the previous Branch's stale summary or timestamp.
    this.summary.set(null);
    this.lastRefreshedAt.set(null);
    this.loading.set(true);
    this.failed.set(false);
    this.selectedBranchId.set(branchId);
  }

  private onSummary(summary: DashboardSummary | null): void {
    this.loading.set(false);
    this.failed.set(false);

    if (summary === null) {
      // The selected Branch was deleted server-side after this view resolved it (a genuine race, not
      // the normal not-found path above, which already screens the URL against the loaded list).
      this.branchNotFound.set(true);
      this.summary.set(null);
      return;
    }

    this.summary.set(summary);
    this.lastRefreshedAt.set(new Date());
  }

  private onError(): void {
    // A poll-tick failure with data already on screen must not blank it out or show a spinner: the
    // stale-but-real summary and its timestamp stay visible (FS-10 §12). Only a failure before any
    // successful load has ever happened surfaces the generic failure state.
    this.loading.set(false);

    if (this.summary() === null) {
      this.failed.set(true);
    }
  }
}
