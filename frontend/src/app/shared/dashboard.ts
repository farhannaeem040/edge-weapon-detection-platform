import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { AuthService } from '../auth/auth.service';
import { LOGIN_ROUTE } from '../auth/auth.routes';
import { BRANCHES_ROUTE } from '../branches/branch.routes';

/**
 * The protected shell a logged-in Admin lands on (IP-01 T-23/T-24), host of the logout action
 * (T-25), and the navigation entry point to the branch views (T-26).
 *
 * It stays deliberately thin. It lives under `shared` because IP-01 §3 defines exactly four Angular
 * modules (`core`, `auth`, `branches`, `shared`), and the shell is not a fifth.
 */
@Component({
  selector: 'app-dashboard',
  imports: [RouterLink],
  template: `
    <section class="dashboard">
      <div class="dashboard__card card">
        <header class="dashboard__header">
          <h2>Dashboard</h2>
          <button
            class="dashboard__logout btn btn--ghost"
            type="button"
            [disabled]="loggingOut()"
            (click)="logout()"
          >
            {{ loggingOut() ? 'Signing out…' : 'Sign out' }}
          </button>
        </header>
        <p class="dashboard__lede">Manage the platform's branches, cameras, and device activation.</p>
        <nav class="dashboard__nav">
          <!-- Behind authGuard like every route it reaches, so this link is never a way in without a
               session — it is navigation, not an authorization decision (FS-01 §10). -->
          <a class="dashboard__branches-link btn btn--primary" [routerLink]="branchesRoute">
            Go to branches
          </a>
        </nav>
      </div>
    </section>
  `,
  styles: `
    .dashboard {
      max-width: 40rem;
      margin: 0 auto;
      padding: var(--space-6) var(--space-4);
    }

    .dashboard__card {
      padding: var(--space-5);
    }

    .dashboard__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-4);
    }

    .dashboard__lede {
      color: var(--color-text-muted);
      margin: var(--space-3) 0 var(--space-4);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardComponent {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly branchesRoute = BRANCHES_ROUTE;
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
