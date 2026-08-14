import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { Branch } from '../branches/branch.models';
import { BranchService } from '../branches/branch.service';
import { AlertStatusBadgeComponent } from '../shared/alert-status-badge';
import { WeaponClassBadgeComponent } from '../shared/weapon-class-badge';
import { AlertListFilter, AlertListItem, AlertSortBy, defaultAlertListFilter } from './alert.models';
import { AlertService } from './alert.service';
import { alertDetailRoute } from './alert.routes';

/** The className filter's allowed values, mirroring `AlertController`'s own whitelist. */
const CLASS_NAME_OPTIONS: readonly string[] = ['gun', 'knife'];

/**
 * The paginated, filterable Alert list (FS-10 §6 "Alert list"; IP-12 T-205–T-207).
 *
 * Filter and pagination state is the URL's query string, not component state: every change navigates
 * to a full replacement set of query params (`navigateToFilter`), and every fetch is driven by
 * `ActivatedRoute.queryParamMap`, so a refresh or the back/forward button restores exactly what was
 * on screen, and there is exactly one source of truth for "what is currently being asked for"
 * (FS-10 §6, explicit URL-persistence requirement).
 *
 * Pagination and filtering both happen on the Backend — this component slices nothing itself, so the
 * page stays responsive regardless of how many Alerts exist in total (FS-10 §13, "2,000+ Alerts must
 * not freeze the browser": only one bounded page is ever held in memory).
 */
@Component({
  selector: 'app-alert-list',
  imports: [
    RouterLink,
    FormsModule,
    DatePipe,
    DecimalPipe,
    WeaponClassBadgeComponent,
    AlertStatusBadgeComponent,
  ],
  template: `
    <section class="alerts">
      <header class="alerts__header page-header">
        <div class="page-header__titles">
          <span class="breadcrumb">Dashboard / Alerts</span>
          <h2 class="page-header__title">Alerts</h2>
        </div>
      </header>

      <form class="alerts__filters card" (ngSubmit)="applyFilterForm()">
        <div class="card__body alerts__filters-grid">
          <div class="field">
            <label class="field__label" for="alerts-filter-branch">Branch</label>
            <!-- Deliberately not [(ngModel)] here: the Branch list loads asynchronously and can
                 resolve after filterBranchId is first set from the URL (manual-review Correction 2),
                 so a plain ngModel write can race a select that has no matching option yet. Per-option
                 [selected] is re-evaluated whenever that option's own binding updates — including the
                 moment it is first created once the Branch list arrives — so the restored selection is
                 never lost to that ordering. -->
            <select
              id="alerts-filter-branch"
              name="branchId"
              (change)="filterBranchId = $any($event.target).value"
            >
              <option value="" [selected]="filterBranchId === ''">All Branches</option>
              @for (branch of branches(); track branch.branchId) {
                <option [value]="branch.branchId" [selected]="branch.branchId === filterBranchId">
                  {{ branch.name }}
                </option>
              }
            </select>
          </div>

          <div class="field">
            <label class="field__label" for="alerts-filter-from">From</label>
            <input
              id="alerts-filter-from"
              type="datetime-local"
              [(ngModel)]="filterFromUtc"
              name="fromUtc"
            />
          </div>

          <div class="field">
            <label class="field__label" for="alerts-filter-to">To</label>
            <input
              id="alerts-filter-to"
              type="datetime-local"
              [(ngModel)]="filterToUtc"
              name="toUtc"
            />
          </div>

          <div class="field">
            <label class="field__label" for="alerts-filter-class">Weapon class</label>
            <select id="alerts-filter-class" [(ngModel)]="filterClassName" name="className">
              <option value="">All classes</option>
              @for (option of classNameOptions; track option) {
                <option [value]="option">{{ option === 'gun' ? 'Gun' : 'Knife' }}</option>
              }
            </select>
          </div>

          <div class="field">
            <label class="field__label" for="alerts-filter-snapshot">Snapshot</label>
            <select
              id="alerts-filter-snapshot"
              [(ngModel)]="filterSnapshotAvailable"
              name="snapshotAvailable"
            >
              <option value="">Any</option>
              <option value="true">Available</option>
              <option value="false">Not available</option>
            </select>
          </div>

          <div class="alerts__filters-actions">
            <button class="btn btn--primary" type="submit">Apply filters</button>
            <button class="btn btn--ghost" type="button" (click)="clearFilters()">Clear</button>
          </div>
        </div>
      </form>

      @if (loading()) {
        <div class="card">
          <p class="alerts__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading Alerts…
          </p>
        </div>
      } @else if (failed()) {
        <div class="card">
          <p class="alerts__status alerts__status--error card__body banner banner--error" role="alert">
            Alerts could not be loaded. Try again.
          </p>
        </div>
      } @else if (items().length === 0) {
        <div class="card">
          <div class="alerts__empty card__body empty-state">
            <p class="alerts__status">No Alerts match the current filters.</p>
          </div>
        </div>
      } @else {
        <div class="card table-scroll">
          <table class="table">
            <thead>
              <tr>
                <th>
                  <button class="alerts__sort" type="button" (click)="toggleSort('detectedAtUtc')">
                    Detected {{ sortIndicator('detectedAtUtc') }}
                  </button>
                </th>
                <th>Class</th>
                <th>Confidence</th>
                <th>Camera</th>
                <th>Status</th>
                <th>Snapshot</th>
              </tr>
            </thead>
            <tbody>
              @for (alert of items(); track alert.alertId) {
                <tr class="alerts__row" [routerLink]="detailRoute(alert)">
                  <td>{{ alert.detectedAtUtc | date: 'medium' }}</td>
                  <td><app-weapon-class-badge [className]="alert.className" /></td>
                  <td>{{ alert.confidence | number: '1.0-2' }}</td>
                  <td>{{ alert.cameraName }}</td>
                  <td><app-alert-status-badge [status]="alert.status" /></td>
                  <td>{{ alert.snapshotAvailable ? 'Available' : 'Not available' }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <nav class="alerts__pagination" aria-label="Alert list pagination">
          <button class="btn btn--ghost" type="button" [disabled]="page() <= 1" (click)="goToPage(page() - 1)">
            Previous
          </button>
          <span class="alerts__page-status status-text">
            Page {{ page() }} of {{ totalPages() }} · {{ totalCount() }} Alerts
          </span>
          <button
            class="btn btn--ghost"
            type="button"
            [disabled]="page() >= totalPages()"
            (click)="goToPage(page() + 1)"
          >
            Next
          </button>
        </nav>
      }
    </section>
  `,
  styles: `
    .alerts__filters {
      margin-bottom: var(--space-5);
    }

    .alerts__filters-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
      gap: var(--space-4);
      align-items: end;
    }

    .alerts__filters-actions {
      display: flex;
      align-items: center;
      gap: var(--space-2);
    }

    .alerts__sort {
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      color: inherit;
      cursor: pointer;
      text-transform: inherit;
      letter-spacing: inherit;
    }

    .alerts__row {
      cursor: pointer;
    }

    .alerts__row:hover {
      background: var(--color-surface-subtle);
    }

    .alerts__pagination {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: var(--space-4);
      margin-top: var(--space-4);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertListComponent implements OnInit {
  private readonly alertService = inject(AlertService);
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly classNameOptions = CLASS_NAME_OPTIONS;

  /**
   * Every Branch, for the filter dropdown only — populated from the real Admin Branch API
   * (`BranchService.list()`), never hard-coded (manual-review Correction 2). A failure to load this
   * list is not surfaced as a page-level error: the Alert list itself still works with "All Branches"
   * and a `branchId` already present in the URL, so the dropdown is simply empty-but-present rather
   * than blocking the page.
   */
  protected readonly branches = signal<Branch[]>([]);

  protected readonly items = signal<AlertListItem[]>([]);
  protected readonly page = signal(1);
  protected readonly totalPages = signal(1);
  protected readonly totalCount = signal(0);
  protected readonly loading = signal(true);
  protected readonly failed = signal(false);

  private currentFilter = defaultAlertListFilter();

  // Two-way-bound form fields, kept separate from `currentFilter` so typing does not refetch until
  // the Admin explicitly applies (FS-10 §6): a filter change is a deliberate action, not a keystroke.
  protected filterBranchId = '';
  protected filterFromUtc = '';
  protected filterToUtc = '';
  protected filterClassName = '';
  protected filterSnapshotAvailable = '';

  ngOnInit(): void {
    this.branchService.list().subscribe({
      next: (branches) => this.branches.set(branches),
      error: () => {},
    });

    this.route.queryParamMap.subscribe((params) => {
      this.currentFilter = filterFromQueryParams(params);
      this.syncFormFromFilter();
      this.fetch(this.currentFilter);
    });
  }

  protected applyFilterForm(): void {
    const filter: AlertListFilter = {
      ...this.currentFilter,
      page: 1,
      branchId: this.filterBranchId || undefined,
      fromUtc: this.filterFromUtc ? new Date(this.filterFromUtc).toISOString() : undefined,
      toUtc: this.filterToUtc ? new Date(this.filterToUtc).toISOString() : undefined,
      className: this.filterClassName || undefined,
      snapshotAvailable:
        this.filterSnapshotAvailable === '' ? undefined : this.filterSnapshotAvailable === 'true',
    };

    this.navigateToFilter(filter);
  }

  protected clearFilters(): void {
    this.navigateToFilter(defaultAlertListFilter());
  }

  protected toggleSort(sortBy: AlertSortBy): void {
    const sortDescending =
      this.currentFilter.sortBy === sortBy ? !this.currentFilter.sortDescending : true;
    this.navigateToFilter({ ...this.currentFilter, sortBy, sortDescending, page: 1 });
  }

  protected sortIndicator(sortBy: AlertSortBy): string {
    if (this.currentFilter.sortBy !== sortBy) {
      return '';
    }
    return this.currentFilter.sortDescending ? '▼' : '▲';
  }

  protected goToPage(page: number): void {
    this.navigateToFilter({ ...this.currentFilter, page });
  }

  protected detailRoute(alert: AlertListItem): string {
    return alertDetailRoute(alert.alertId);
  }

  /**
   * Navigates to the new filter's query params. The route subscription (`ngOnInit`) is what actually
   * fetches — this only changes the URL, so the URL stays the single source of truth for what the
   * list is currently showing (FS-10 §6).
   */
  private navigateToFilter(filter: AlertListFilter): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: filterToQueryParams(filter),
    });
  }

  private syncFormFromFilter(): void {
    this.filterBranchId = this.currentFilter.branchId ?? '';
    this.filterFromUtc = this.currentFilter.fromUtc ? toDateTimeLocal(this.currentFilter.fromUtc) : '';
    this.filterToUtc = this.currentFilter.toUtc ? toDateTimeLocal(this.currentFilter.toUtc) : '';
    this.filterClassName = this.currentFilter.className ?? '';
    this.filterSnapshotAvailable =
      this.currentFilter.snapshotAvailable === undefined
        ? ''
        : String(this.currentFilter.snapshotAvailable);
  }

  private fetch(filter: AlertListFilter): void {
    this.loading.set(true);
    this.failed.set(false);

    this.alertService.listAlerts(filter).subscribe({
      next: (response) => {
        this.loading.set(false);
        this.items.set(response.items);
        this.page.set(response.page);
        this.totalPages.set(Math.max(1, response.totalPages));
        this.totalCount.set(response.totalCount);
      },
      // A 401 has already been handled globally (T-25) before this runs.
      error: () => {
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }
}

/** Reads the filter from the URL's query params, falling back to the default for anything absent. */
function filterFromQueryParams(params: { get(name: string): string | null }): AlertListFilter {
  const defaults = defaultAlertListFilter();

  const page = toPositiveInt(params.get('page')) ?? defaults.page;
  const pageSize = toPositiveInt(params.get('pageSize')) ?? defaults.pageSize;
  const sortByRaw = params.get('sortBy');
  const sortBy: AlertSortBy = sortByRaw === 'receivedAtUtc' ? 'receivedAtUtc' : defaults.sortBy;
  const sortDescendingRaw = params.get('sortDescending');
  const sortDescending = sortDescendingRaw === null ? defaults.sortDescending : sortDescendingRaw !== 'false';

  const snapshotAvailableRaw = params.get('snapshotAvailable');

  return {
    page,
    pageSize,
    sortBy,
    sortDescending,
    fromUtc: params.get('fromUtc') ?? undefined,
    toUtc: params.get('toUtc') ?? undefined,
    className: params.get('className') ?? undefined,
    branchId: params.get('branchId') ?? undefined,
    cameraId: params.get('cameraId') ?? undefined,
    status: params.get('status') ?? undefined,
    snapshotAvailable: snapshotAvailableRaw === null ? undefined : snapshotAvailableRaw === 'true',
  };
}

/** Writes the filter into query params, omitting anything at its default/absent value. */
function filterToQueryParams(filter: AlertListFilter): Record<string, string | null> {
  const defaults = defaultAlertListFilter();

  return {
    page: filter.page === defaults.page ? null : String(filter.page),
    pageSize: filter.pageSize === defaults.pageSize ? null : String(filter.pageSize),
    sortBy: filter.sortBy === defaults.sortBy ? null : filter.sortBy,
    sortDescending:
      filter.sortDescending === defaults.sortDescending ? null : String(filter.sortDescending),
    fromUtc: filter.fromUtc ?? null,
    toUtc: filter.toUtc ?? null,
    className: filter.className ?? null,
    branchId: filter.branchId ?? null,
    cameraId: filter.cameraId ?? null,
    status: filter.status ?? null,
    snapshotAvailable: filter.snapshotAvailable === undefined ? null : String(filter.snapshotAvailable),
  };
}

function toPositiveInt(value: string | null): number | undefined {
  if (value === null) {
    return undefined;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

/** Converts a stored UTC ISO string to the local `datetime-local` input value it should display. */
function toDateTimeLocal(isoUtc: string): string {
  const date = new Date(isoUtc);
  const offsetMs = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

