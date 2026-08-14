import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** The states this badge can render: the two weapon classes the model detects, plus a defensive third. */
type BadgeState = 'gun' | 'knife' | 'unknown';

/** The only class names this model currently produces (mirrors `AlertController`'s own whitelist). */
const LABELS: Readonly<Record<BadgeState, string>> = {
  gun: 'Gun',
  knife: 'Knife',
  unknown: 'Unknown class',
};

/**
 * The weapon class an Alert or a suppressed-detection count is for (FS-10 §6/§9 — never rendered
 * with colour alone, per the task's explicit WCAG requirement: each state carries its own visible
 * text, so the badge reads the same on a monochrome display or to a screen reader).
 *
 * The input is the raw `className` string exactly as the Backend sent it. A value outside the two
 * known classes renders as "Unknown class" rather than being echoed or guessed at — this platform's
 * model only ever produces `gun`/`knife` (FS-09 §7), so anything else is a defensive fallback, not an
 * expected case.
 */
@Component({
  selector: 'app-weapon-class-badge',
  template: `
    <span class="weapon-class-badge badge" [class]="'badge--' + modifier()">{{ label() }}</span>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WeaponClassBadgeComponent {
  readonly className = input.required<string>();

  protected readonly state = computed<BadgeState>(() => {
    const value = this.className().toLowerCase();
    return value === 'gun' || value === 'knife' ? value : 'unknown';
  });

  protected readonly label = computed(() => LABELS[this.state()]);

  /** `unknown` maps to a distinct CSS modifier name so it never collides with a real class name. */
  protected readonly modifier = computed(() =>
    this.state() === 'unknown' ? 'unknown-class' : this.state(),
  );
}
