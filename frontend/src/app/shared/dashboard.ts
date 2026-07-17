import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { AuthService } from '../auth/auth.service';
import { LOGIN_ROUTE } from '../auth/auth.routes';

/**
 * The minimum protected shell a logged-in Admin lands on (IP-01 T-23/T-24), and the host of the
 * logout action (T-25).
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
      <header class="dashboard__header">
        <h2>Dashboard</h2>
        <button class="dashboard__logout" type="button" [disabled]="loggingOut()" (click)="logout()">
          {{ loggingOut() ? 'Signing out…' : 'Sign out' }}
        </button>
      </header>
      <p>You are signed in. Branch management views arrive in a later task.</p>
    </section>
  `,
  styles: `
    .dashboard__header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardComponent {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly loggingOut = signal(false);

  /**
   * Ends the session and returns to the login view.
   *
   * `AuthService.logout()` asks the Backend to revoke the server-side `AdminSession` and discards
   * the local token whether or not that request succeeds, so both outcomes land here (FS-01 §7).
   * Neither the token nor the logout response is ever displayed.
   */
  protected logout(): void {
    if (this.loggingOut()) {
      return;
    }

    this.loggingOut.set(true);

    this.authService.logout().subscribe({
      next: () => this.leave(),
      // Unreachable in practice — logout() absorbs Backend failures — but a redirect must never
      // depend on the Backend having answered.
      error: () => this.leave(),
    });
  }

  private leave(): void {
    this.loggingOut.set(false);
    void this.router.navigateByUrl(LOGIN_ROUTE);
  }
}
