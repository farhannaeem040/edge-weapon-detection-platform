import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet, Router } from '@angular/router';

import { AuthService } from '../auth/auth.service';
import { LOGIN_ROUTE } from '../auth/auth.routes';
import { BRANCHES_ROUTE } from '../branches/branch.routes';

/**
 * The authenticated application shell (Stitch "Operations Overview" chrome, applied as styling only).
 *
 * It provides the sidebar (brand + navigation), the top header, the Sign-out action, and the content
 * container into which the protected routes render. It is a layout host: it wraps the branch views
 * via a parent route in `app.routes.ts`, so every authenticated view shares one frame without each
 * view re-declaring it.
 *
 * **Navigation shows only implemented features.** The sole nav item is Branches — the one protected
 * area that exists. The Stitch sidebar's other entries (Live monitoring, Alerts, Incidents, Cameras,
 * Edge devices, Analytics, System health, Users and access, Settings) back no implemented feature and
 * are deliberately absent; no dead links, no "coming soon" stubs (SCREEN-INVENTORY.md). `/dashboard`
 * still exists as a route but carries no meaningful content beyond what the shell already offers, so
 * it is not surfaced as navigation either.
 *
 * Sign-out mirrors the established logout contract: it asks the Backend to revoke the server-side
 * `AdminSession`, discards the local token whatever the outcome, and returns to the login view
 * (FS-01 §7). Neither the token nor the logout response is ever displayed.
 */
@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="shell" [class.shell--nav-open]="navOpen()">
      <!-- Off-canvas backdrop (mobile only): tapping it closes the sidebar. -->
      <div
        class="shell__scrim"
        [hidden]="!navOpen()"
        (click)="closeNav()"
        aria-hidden="true"
      ></div>

      <aside class="shell__sidebar" [attr.aria-hidden]="null">
        <div class="shell__brand">
          <span class="shell__logo" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="24" height="24" focusable="false">
              <path
                fill="currentColor"
                d="M12 2 4 5v6c0 4.4 3 8.5 8 11 5-2.5 8-6.6 8-11V5l-8-3Z"
                opacity="0.18"
              />
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linejoin="round"
                stroke-linecap="round"
                d="M12 2 4 5v6c0 4.4 3 8.5 8 11 5-2.5 8-6.6 8-11V5l-8-3Z M9 12l2 2 4-4"
              />
            </svg>
          </span>
          <span class="shell__brand-text">
            <span class="shell__brand-name">LJMU AI</span>
            <span class="shell__brand-sub">Security Platform</span>
          </span>
        </div>

        <nav class="shell__nav" aria-label="Primary">
          <a
            class="shell__nav-link"
            [routerLink]="branchesRoute"
            routerLinkActive="shell__nav-link--active"
            (click)="closeNav()"
          >
            <svg
              class="shell__nav-icon"
              viewBox="0 0 24 24"
              width="20"
              height="20"
              aria-hidden="true"
              focusable="false"
            >
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M4 10 12 4l8 6 M6 9v10h12V9 M10 19v-5h4v5"
              />
            </svg>
            <span>Branches</span>
          </a>
        </nav>

        <div class="shell__sidebar-foot">
          <span class="shell__status-dot" aria-hidden="true"></span>
          <span>System online</span>
        </div>
      </aside>

      <div class="shell__main">
        <header class="shell__header">
          <button
            class="shell__nav-toggle icon-btn"
            type="button"
            [attr.aria-expanded]="navOpen()"
            aria-controls="shell-sidebar"
            aria-label="Toggle navigation"
            (click)="toggleNav()"
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
              <path
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                d="M4 7h16 M4 12h16 M4 17h16"
              />
            </svg>
          </button>

          <span class="shell__header-title">Branch management</span>

          <button
            class="shell__logout btn btn--ghost"
            type="button"
            [disabled]="loggingOut()"
            (click)="logout()"
          >
            {{ loggingOut() ? 'Signing out…' : 'Sign out' }}
          </button>
        </header>

        <main class="shell__content" id="shell-content">
          <router-outlet />
        </main>
      </div>
    </div>
  `,
  styles: `
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: var(--sidebar-width) 1fr;
      background: var(--color-bg);
    }

    .shell__sidebar {
      grid-column: 1;
      display: flex;
      flex-direction: column;
      gap: var(--space-5);
      padding: var(--space-5) var(--space-3);
      background: var(--color-charcoal);
      color: var(--color-text-on-dark);
      position: sticky;
      top: 0;
      height: 100vh;
    }

    .shell__brand {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      padding: 0 var(--space-2);
    }

    .shell__logo {
      display: inline-flex;
      color: var(--color-secondary);
    }

    .shell__brand-text {
      display: flex;
      flex-direction: column;
      line-height: 1.15;
    }

    .shell__brand-name {
      font-family: var(--font-heading);
      font-weight: var(--weight-semibold);
      font-size: 1rem;
    }

    .shell__brand-sub {
      font-size: var(--text-label);
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--color-text-on-dark-muted);
    }

    .shell__nav {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
    }

    .shell__nav-link {
      display: flex;
      align-items: center;
      gap: var(--space-3);
      padding: 0.6rem 0.75rem;
      border-radius: var(--radius);
      border-left: 2px solid transparent;
      color: var(--color-text-on-dark-muted);
      font-size: var(--text-sm);
      font-weight: var(--weight-medium);
      text-decoration: none;
      transition: background var(--transition), color var(--transition);
    }

    .shell__nav-link:hover {
      background: var(--color-charcoal-2);
      color: var(--color-text-on-dark);
      text-decoration: none;
    }

    .shell__nav-link--active {
      background: var(--color-charcoal-2);
      color: #fff;
      border-left-color: var(--color-secondary);
    }

    .shell__nav-icon {
      flex: none;
    }

    .shell__sidebar-foot {
      margin-top: auto;
      display: flex;
      align-items: center;
      gap: var(--space-2);
      padding: var(--space-2);
      font-size: var(--text-label);
      color: var(--color-text-on-dark-muted);
    }

    .shell__status-dot {
      width: 0.5rem;
      height: 0.5rem;
      border-radius: 50%;
      background: var(--color-secondary);
    }

    .shell__main {
      grid-column: 2;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }

    .shell__header {
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      align-items: center;
      gap: var(--space-3);
      height: var(--header-height);
      padding: 0 var(--space-6);
      background: var(--color-surface);
      border-bottom: 1px solid var(--color-border);
    }

    .shell__header-title {
      font-family: var(--font-heading);
      font-weight: var(--weight-medium);
      color: var(--color-text-muted);
    }

    .shell__logout {
      margin-left: auto;
    }

    .shell__nav-toggle {
      display: none;
      color: var(--color-text-muted);
    }

    .shell__content {
      flex: 1;
      width: 100%;
      max-width: var(--layout-max);
      margin: 0 auto;
      padding: var(--space-6);
    }

    .shell__scrim {
      display: none;
    }

    /* Tablet / mobile: sidebar becomes an off-canvas panel, revealed by the header toggle. Required
       actions stay reachable; the toggle is keyboard operable and the nav keeps its focus styles. */
    @media (max-width: 900px) {
      .shell {
        grid-template-columns: 1fr;
      }

      .shell__sidebar {
        position: fixed;
        z-index: 20;
        top: 0;
        left: 0;
        width: var(--sidebar-width);
        transform: translateX(-100%);
        transition: transform var(--transition);
      }

      .shell--nav-open .shell__sidebar {
        transform: translateX(0);
      }

      .shell__main {
        grid-column: 1;
      }

      .shell__nav-toggle {
        display: inline-flex;
      }

      .shell__scrim {
        display: block;
        position: fixed;
        inset: 0;
        z-index: 15;
        background: rgba(23, 33, 28, 0.4);
      }

      .shell__content {
        padding: var(--space-5) var(--space-4);
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .shell__sidebar {
        transition: none;
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShellComponent {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly branchesRoute = BRANCHES_ROUTE;
  protected readonly loggingOut = signal(false);

  /** Whether the off-canvas sidebar is open (mobile only; desktop shows it permanently). */
  protected readonly navOpen = signal(false);

  protected toggleNav(): void {
    this.navOpen.update((open) => !open);
  }

  protected closeNav(): void {
    this.navOpen.set(false);
  }

  /** Ends the session and returns to the login view. See the class comment for the contract. */
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
