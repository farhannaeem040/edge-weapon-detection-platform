import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Branch } from './branch.models';
import { BranchService } from './branch.service';
import { BRANCH_CREATE_ROUTE, branchDetailRoute, branchEditRoute } from './branch.routes';
import { DeviceStatusBadgeComponent } from './device-status-badge';

/**
 * The branch list (IP-01 T-26; FS-02 §10.3, AC-1).
 *
 * It shows only summary data — enough for an Admin to recognise a branch and drill into it — and
 * links each row to that branch's detail route. The cameras and the device's public identifier are
 * detail-view concerns; a list is not the place to spread them.
 *
 * The four states (loading, failed, empty, loaded) are modelled explicitly rather than inferred
 * from an empty array, because "no branches exist yet" and "the request failed" are different
 * things an Admin must be able to tell apart.
 */
@Component({
  selector: 'app-branch-list',
  imports: [RouterLink, DeviceStatusBadgeComponent],
  template: `
    <section class="branches">
      <header class="branches__header">
        <h2>Branches</h2>
        <a class="branches__create" [routerLink]="createRoute">Create branch</a>
        <a class="branches__back" routerLink="/dashboard">Dashboard</a>
      </header>

      @if (loading()) {
        <p class="branches__status">Loading branches…</p>
      } @else if (failed()) {
        <!-- Deliberately generic: the Backend's own error text is never surfaced (FS-02 §11). -->
        <p class="branches__status branches__status--error" role="alert">
          Branches could not be loaded. Try again.
        </p>
      } @else if (branches().length === 0) {
        <p class="branches__status">No branches have been created yet.</p>
      } @else {
        <ul class="branches__list">
          @for (branch of branches(); track branch.branchId) {
            <li class="branches__item">
              <a class="branches__link" [routerLink]="detailRoute(branch)">{{ branch.name }}</a>
              <span class="branches__address">{{ branch.address }}</span>
              <!-- The explicit status field, and nothing derived from deviceId (FS-02 §10.3). -->
              <app-device-status-badge
                class="branches__status-badge"
                [status]="branch.device.activationStatus"
              />
              <!-- Edit/Delete actions sit together, beside their own branch, so each icon is
                   unambiguously associated with that row (FS-03 §6.1, AC-15). The delete control is
                   added by T-46; edit is the pencil link here. Each is a real, keyboard-focusable
                   link/button with an aria-label naming the branch and a matching title tooltip, so
                   it is never "an icon alone" to assistive tech (FS-03 §8, AC-16). -->
              <span class="branches__actions">
                <a
                  class="branches__action branches__edit"
                  [routerLink]="editRoute(branch)"
                  [attr.aria-label]="'Edit branch ' + branch.name"
                  [title]="'Edit branch ' + branch.name"
                >
                  <!-- Pencil. Decorative to assistive tech; the link's aria-label carries meaning. -->
                  <svg
                    class="branches__icon"
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                    aria-hidden="true"
                    focusable="false"
                  >
                    <path
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3z M13.5 6.5l3 3"
                    />
                  </svg>
                </a>
              </span>
            </li>
          }
        </ul>
      }
    </section>
  `,
  styles: `
    .branches__header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
    }

    .branches__list {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .branches__item {
      display: flex;
      align-items: baseline;
      gap: 1rem;
      padding: 0.5rem 0;
    }

    .branches__address {
      flex: 1;
    }

    .branches__actions {
      display: flex;
      align-items: center;
      gap: 0.25rem;
    }

    /* An adequate click/tap target around the 20px glyph (FS-03 §8, AC-16). */
    .branches__action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 2.25rem;
      height: 2.25rem;
      color: inherit;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchListComponent implements OnInit {
  private readonly branchService = inject(BranchService);

  /**
   * Rendered in the header, outside the loading/failed/empty branches, so an Admin with no branches
   * yet — the very Admin who needs it — still has the create action in front of them (T-27).
   */
  protected readonly createRoute = BRANCH_CREATE_ROUTE;

  protected readonly branches = signal<Branch[]>([]);
  protected readonly loading = signal(true);
  protected readonly failed = signal(false);

  ngOnInit(): void {
    this.branchService.list().subscribe({
      next: (branches) => {
        this.branches.set(branches);
        this.loading.set(false);
      },
      error: () => {
        // A 401 has already been handled globally (T-25): the session-expiry interceptor clears the
        // token and redirects to /login before this runs, and rethrows so the view still settles
        // into a terminal state rather than showing a spinner forever behind the redirect.
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }

  protected detailRoute(branch: Branch): string {
    return branchDetailRoute(branch.branchId);
  }

  protected editRoute(branch: Branch): string {
    return branchEditRoute(branch.branchId);
  }
}
