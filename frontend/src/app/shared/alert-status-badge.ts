import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * The states this badge can render: the one status this platform currently produces (`New` —
 * `AlertStatus` has no other member yet; FR-DET-008/009 status transitions are not implemented,
 * FS-10 §1 explicitly excluded), plus a defensive fallback for anything else the Backend might send.
 */
type BadgeState = 'new' | 'unknown';

const LABELS: Readonly<Record<BadgeState, string>> = {
  new: 'New',
  unknown: 'Unknown status',
};

/**
 * An Alert's `status` (FS-10 §9.2/§9.3). Every Alert on screen is one the Backend accepted past the
 * Branch-day quota (FS-09/IP-11) — a quota-suppressed detection never becomes an Alert, so this badge
 * never renders a "suppressed" state; suppression counts are shown separately on the dashboard summary
 * (`dashboard-summary.ts`), never as if they were a kind of Alert.
 */
@Component({
  selector: 'app-alert-status-badge',
  template: `
    <span class="alert-status-badge badge" [class]="'badge--' + state()">{{ label() }}</span>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertStatusBadgeComponent {
  readonly status = input.required<string>();

  protected readonly state = computed<BadgeState>(() =>
    this.status().toLowerCase() === 'new' ? 'new' : 'unknown',
  );

  protected readonly label = computed(() => LABELS[this.state()]);
}
