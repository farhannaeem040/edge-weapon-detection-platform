import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The minimum protected shell a logged-in Admin lands on (IP-01 T-23/T-24).
 *
 * It is deliberately a placeholder: FS-02's branch list/detail views are T-26's work, and T-24
 * needs *some* protected route to apply the guard to. It lives under `shared` because IP-01 §3
 * defines exactly four Angular modules (`core`, `auth`, `branches`, `shared`) and a temporary
 * placeholder does not justify a fifth.
 */
@Component({
  selector: 'app-dashboard',
  template: `
    <section class="dashboard">
      <h2>Dashboard</h2>
      <p>You are signed in. Branch management views arrive in a later task.</p>
    </section>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardComponent {}
