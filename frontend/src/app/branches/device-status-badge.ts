import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { DeviceActivationStatus } from './branch.models';

/**
 * The states this badge can render: the Backend's two, plus the defensive third (see `state`).
 */
type BadgeState = DeviceActivationStatus | 'Unknown';

/** The statuses the Backend is contracted to send, as it serializes them (FS-02 §10.3). */
const KNOWN_STATUSES: readonly string[] = ['Unactivated', 'Activated'];

/** The visible label per state. Never the raw input — an unrecognised value is not echoed. */
const LABELS: Readonly<Record<BadgeState, string>> = {
  Unactivated: 'Unactivated',
  Activated: 'Activated',
  Unknown: 'Unknown',
};

/**
 * The description read out after the label, carrying what the label alone leaves implicit. Not shown
 * visually — the label is the visual signal — but part of the badge's text, so it reaches anyone
 * using a screen reader rather than only those who can read the colour.
 */
const DESCRIPTIONS: Readonly<Record<BadgeState, string>> = {
  Unactivated:
    'This branch’s Device has not been activated yet. It has no Device ID until it activates.',
  Activated: 'This branch’s Device has been activated.',
  Unknown: 'This branch’s Device activation state could not be determined. Reload the page.',
};

/**
 * A branch Device's activation state (IP-01 T-29; FS-02 §10.3, AC-7).
 *
 * T-26 rendered the status inline in both the list and the detail view. Two copies of the same rule
 * is one too many — this is that rendering, extracted, so the list and the detail agree by
 * construction rather than by coincidence, and the accessibility and defensive behaviour below are
 * written once.
 *
 * **The input is the status, not the Device.** `activationStatus` is the explicit field and the only
 * thing activation state may be branched on: never the presence of `deviceId`, never a camera or
 * branch condition, never anything the client derives (FS-02 §10.3). Taking the status alone makes
 * that structural rather than a promise — this component is never handed a `deviceId`, a
 * `DeviceRecordId`, an Activation Key, or a `ProtectedSharedSecret`, so it cannot render or infer
 * from one. The public Device ID stays where it was: on the detail view, which owns it.
 *
 * Colour is not the signal. Each state renders its own visible text, so the badge reads the same
 * with styles off, on a monochrome display, or to a screen reader; the tint is decoration on top of
 * a label that already says the thing (WCAG 1.4.1).
 */
@Component({
  selector: 'app-device-status-badge',
  template: `
    <span class="device-status" [class]="'device-status--' + stateModifier()">
      <!-- Read out but not shown: on screen the badge sits beside the branch it describes, which a
           linear reading order does not convey on its own. -->
      <span class="device-status__context">Device status:</span>
      <span class="device-status__label">{{ label() }}</span>
      <span class="device-status__description">{{ description() }}</span>
    </span>
  `,
  styles: `
    .device-status {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.15rem 0.6rem;
      border: 1px solid;
      border-radius: var(--radius-pill, 9999px);
      font-family: var(--font-heading, sans-serif);
      font-size: var(--text-label, 0.75rem);
      font-weight: var(--weight-medium, 500);
      white-space: nowrap;
    }

    /* A leading dot reinforces the status without relying on colour to carry meaning on its own. */
    .device-status::before {
      content: '';
      width: 0.45rem;
      height: 0.45rem;
      border-radius: 50%;
      background: currentColor;
      flex: none;
    }

    /* Colour reinforces the label; it never carries the meaning by itself. Each pairing is a dark
       foreground on a pale background, kept well clear of the 4.5:1 minimum for this text size. */
    .device-status--activated {
      border-color: #b7ddc4;
      background-color: var(--color-success-bg, #e6f4ea);
      color: #10441f;
    }

    .device-status--unactivated {
      border-color: #e6cf8a;
      background-color: var(--color-warning-bg, #fdf3d7);
      color: #4a3800;
    }

    .device-status--unknown {
      border-color: var(--color-border, #d0d7de);
      background-color: var(--color-neutral-bg, #eef1f4);
      color: var(--color-neutral-text, #24292f);
    }

    /* Present to assistive technology, absent from the visual badge. Not display:none or
       visibility:hidden, which would remove it from the accessibility tree along with the
       screen. */
    .device-status__context,
    .device-status__description {
      position: absolute;
      width: 1px;
      height: 1px;
      margin: -1px;
      padding: 0;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
      border: 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DeviceStatusBadgeComponent {
  /**
   * The Device's `activationStatus`, exactly as the Backend sent it.
   *
   * Typed to the two contracted values so a call site cannot pass something invented, while `state`
   * still copes with a value that arrives anyway — the type is a compile-time claim about the
   * Backend, and an HTTP response is not type-checked.
   */
  readonly status = input.required<DeviceActivationStatus>();

  /**
   * The status narrowed to a state this component renders.
   *
   * A value outside the contract becomes `Unknown`, never `Activated`: a misread status that
   * over-reports activation would tell an Admin a branch is live when nothing says it is. `Unknown`
   * is the honest, harmless reading, and the offending value is not put on screen — it came from
   * outside and this view is not the place to find out what it was.
   */
  protected readonly state = computed<BadgeState>(() => {
    const status: string = this.status();
    return KNOWN_STATUSES.includes(status) ? (status as DeviceActivationStatus) : 'Unknown';
  });

  protected readonly label = computed(() => LABELS[this.state()]);
  protected readonly description = computed(() => DESCRIPTIONS[this.state()]);
  protected readonly stateModifier = computed(() => this.state().toLowerCase());
}
