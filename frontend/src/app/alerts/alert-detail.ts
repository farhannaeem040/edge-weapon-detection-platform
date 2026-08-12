import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

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
              <!-- FS-10 explicitly excludes snapshot capture/upload/retrieval from this feature: no
                   image endpoint exists to fetch from, so even an Alert reporting availability shows
                   the same honest statement rather than an <img> pointed at nothing. -->
              <p class="alert-detail__status status-text">
                A snapshot was captured for this Alert. Snapshot retrieval is not yet available in this
                view.
              </p>
            } @else {
              <app-alert-snapshot-placeholder />
            }
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
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertDetailComponent implements OnInit {
  private readonly alertService = inject(AlertService);
  private readonly route = inject(ActivatedRoute);

  protected readonly alertsRoute = ALERTS_ROUTE;

  protected readonly alert = signal<AlertDetail | null>(null);
  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly failed = signal(false);

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
      },
      error: () => {
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }
}
