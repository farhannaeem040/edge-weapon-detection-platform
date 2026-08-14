import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { BRANCH_DETAIL_MONITORING_TAB, BRANCH_DETAIL_TAB_PARAM } from '../branches/branch-detail';
import { branchDetailRoute } from '../branches/branch.routes';
import {
  LIVE_MONITORING_CAMERA_PARAM,
  LIVE_MONITORING_MODE_PARAM,
} from '../branches/live-monitoring';
import { AlertStatusBadgeComponent } from '../shared/alert-status-badge';
import { WeaponClassBadgeComponent } from '../shared/weapon-class-badge';
import { AlertSnapshotPlaceholderComponent } from './alert-snapshot-placeholder';
import { ALERTS_ROUTE, ALERT_ID_PARAM } from './alert.routes';
import { AlertDetail } from './alert.models';
import { AlertService } from './alert.service';

/**
 * One Alert in full (FS-10 §6 "Alert detail"; IP-12 T-208). Mirrors `branch-detail.ts`'s
 * loading/notFound/failed/loaded state machine.
 *
 * Renders only fields the API returns — detection/receipt time, delivery latency, class, confidence,
 * branch, camera, device, status, and snapshot availability. No live video, no siren control, no
 * status-transition action (Acknowledge/False positive), and no snapshot upload path: none of those
 * are backed by an approved contract for this feature (FS-10 §1, explicitly excluded).
 */
@Component({
  selector: 'app-alert-detail',
  imports: [
    RouterLink,
    DatePipe,
    DecimalPipe,
    WeaponClassBadgeComponent,
    AlertStatusBadgeComponent,
    AlertSnapshotPlaceholderComponent,
  ],
  template: `
    <section class="alert-detail">
      <header class="alert-detail__header page-header">
        <div class="page-header__titles">
          <a class="alert-detail__back breadcrumb" [routerLink]="alertsRoute">← Alerts</a>
          <h2 class="page-header__title">Alert</h2>
        </div>
      </header>

      @if (loading()) {
        <div class="card">
          <p class="alert-detail__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading Alert…
          </p>
        </div>
      } @else if (notFound()) {
        <div class="card">
          <p class="alert-detail__status card__body banner banner--info" role="alert">
            That Alert was not found.
          </p>
        </div>
      } @else if (failed()) {
        <div class="card">
          <p
            class="alert-detail__status alert-detail__status--error card__body banner banner--error"
            role="alert"
          >
            The Alert could not be loaded. Try again.
          </p>
        </div>
      } @else if (alert(); as alert) {
        <div class="alert-detail__grid">
          <section class="alert-detail__card card">
            <header class="card__header"><h3>Detection</h3></header>
            <dl class="alert-detail__fields card__body">
              <dt>Class</dt>
              <dd><app-weapon-class-badge [className]="alert.className" /></dd>

              <dt>Confidence</dt>
              <dd>{{ alert.confidence | number: '1.0-2' }}</dd>

              <dt>Status</dt>
              <dd><app-alert-status-badge [status]="alert.status" /></dd>

              <dt>Detected</dt>
              <dd>{{ alert.detectedAtUtc | date: 'medium' }}</dd>

              <dt>Received</dt>
              <dd>{{ alert.receivedAtUtc | date: 'medium' }}</dd>

              <dt>Delivery latency</dt>
              <dd>{{ alert.deliveryLatencySeconds | number: '1.0-1' }}s</dd>
            </dl>
          </section>

          <section class="alert-detail__card card">
            <header class="card__header"><h3>Source</h3></header>
            <dl class="alert-detail__fields card__body">
              <dt>Branch</dt>
              <dd>{{ alert.branchName }}</dd>

              <dt>Camera</dt>
              <dd>{{ alert.cameraName }}</dd>
            </dl>
          </section>
        </div>

        <section class="alert-detail__snapshot alert-detail__card card">
          <header class="card__header"><h3>Snapshot</h3></header>
          <div class="card__body">
            @if (alert.snapshotAvailable) {
              @if (snapshotLoading()) {
                <p class="alert-detail__status status-text">
                  <span class="spinner" aria-hidden="true"></span> Loading snapshot…
                </p>
              } @else if (snapshotFailed()) {
                <p class="alert-detail__status banner banner--error" role="alert">
                  Snapshot evidence could not be loaded.
                </p>
              } @else if (snapshotUrl()) {
                <img
                  class="alert-detail__snapshot-image"
                  [src]="snapshotUrl()"
                  alt="Captured snapshot for this Alert, {{ alert.className }} detected on camera {{
                    alert.cameraName
                  }}"
                />
              }
            } @else {
              <app-alert-snapshot-placeholder />
            }
          </div>
        </section>

        <!-- FS-14 §5, IP-16 T-13: deep-links into the Branch Live Monitoring tab, preselected —
             this view never embeds its own player, it only navigates (see live-monitoring.ts). -->
        <section class="alert-detail__live-actions alert-detail__card card">
          <header class="card__header"><h3>Live View</h3></header>
          <div class="card__body alert-detail__live-buttons">
            <a
              class="alert-detail__view-live-camera btn btn--secondary"
              [routerLink]="branchDetailRoute(alert.branchId)"
              [queryParams]="liveViewQueryParams(alert.cameraId, 'monitoring')"
            >
              View Live Camera
            </a>
            <a
              class="alert-detail__view-live-inference btn btn--secondary"
              [routerLink]="branchDetailRoute(alert.branchId)"
              [queryParams]="liveViewQueryParams(alert.cameraId, 'inference')"
            >
              View Live Inference
            </a>
          </div>
        </section>
      }
    </section>
  `,
  styles: `
    .alert-detail__grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
      gap: var(--space-5);
      margin-bottom: var(--space-5);
    }

    .alert-detail__fields {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: var(--space-2) var(--space-5);
      margin: 0;
      align-items: center;
    }

    .alert-detail__fields dt {
      font-family: var(--font-heading);
      font-size: var(--text-label);
      font-weight: var(--weight-medium);
      color: var(--color-text-faint);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }

    .alert-detail__fields dd {
      margin: 0;
      color: var(--color-text);
    }

    .alert-detail__snapshot-image {
      display: block;
      max-width: 100%;
      width: 100%;
      max-height: 32rem;
      height: auto;
      object-fit: contain;
      border-radius: var(--radius);
    }

    .alert-detail__live-buttons {
      display: flex;
      gap: var(--space-2);
      flex-wrap: wrap;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertDetailComponent implements OnInit, OnDestroy {
  private readonly alertService = inject(AlertService);
  private readonly route = inject(ActivatedRoute);

  protected readonly alertsRoute = ALERTS_ROUTE;

  protected readonly alert = signal<AlertDetail | null>(null);
  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly failed = signal(false);

  protected readonly snapshotUrl = signal<string | null>(null);
  protected readonly snapshotLoading = signal(false);
  protected readonly snapshotFailed = signal(false);

  /** FS-14 §5, IP-16 T-13. Exposed so the template can build each "View Live …" link's target. */
  protected readonly branchDetailRoute = branchDetailRoute;

  protected liveViewQueryParams(cameraId: string, mode: 'monitoring' | 'inference') {
    return {
      [BRANCH_DETAIL_TAB_PARAM]: BRANCH_DETAIL_MONITORING_TAB,
      [LIVE_MONITORING_CAMERA_PARAM]: cameraId,
      [LIVE_MONITORING_MODE_PARAM]: mode,
    };
  }

  ngOnInit(): void {
    const alertId = this.route.snapshot.paramMap.get(ALERT_ID_PARAM);

    if (!alertId) {
      this.loading.set(false);
      this.notFound.set(true);
      return;
    }

    this.alertService.getAlert(alertId).subscribe({
      next: (alert) => {
        this.loading.set(false);

        if (alert === null) {
          this.notFound.set(true);
          return;
        }

        this.alert.set(alert);

        if (alert.snapshotAvailable) {
          this.loadSnapshot(alertId);
        }
      },
      error: () => {
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }

  ngOnDestroy(): void {
    this.revokeSnapshotUrl();
  }

  private loadSnapshot(alertId: string): void {
    this.snapshotLoading.set(true);
    this.snapshotFailed.set(false);

    this.alertService.getSnapshot(alertId).subscribe({
      next: (blob) => {
        this.revokeSnapshotUrl();
        this.snapshotUrl.set(URL.createObjectURL(blob));
        this.snapshotLoading.set(false);
      },
      error: () => {
        this.revokeSnapshotUrl();
        this.snapshotLoading.set(false);
        this.snapshotFailed.set(true);
      },
    });
  }

  private revokeSnapshotUrl(): void {
    const current = this.snapshotUrl();
    if (current) {
      URL.revokeObjectURL(current);
      this.snapshotUrl.set(null);
    }
  }
}
