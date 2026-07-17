import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Branch } from './branch.models';
import { BranchService } from './branch.service';
import { BranchDeleteConfirmComponent } from './branch-delete-confirm';
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
  imports: [RouterLink, DeviceStatusBadgeComponent, BranchDeleteConfirmComponent],
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

                <!-- The delete control is a button, not the branch name: destructive actions must
                     never ride on the row's own link (FS-03 §6.1). It only opens the confirmation
                     (below) — the delete request is issued from there, never on this click. -->
                <button
                  class="branches__action branches__delete"
                  type="button"
                  [attr.aria-label]="'Delete branch ' + branch.name"
                  [title]="'Delete branch ' + branch.name"
                  (click)="startDelete(branch)"
                >
                  <!-- Trash/bin. Decorative; the button's aria-label carries meaning. -->
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
                      d="M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3"
                    />
                  </svg>
                </button>
              </span>
            </li>
          }
        </ul>
      }

      @if (deleteFailed()) {
        <!-- One generic message for a delete that failed (a 500 or a dropped connection); no Backend
             text and no status code (FS-03 §12). A 401 never reaches here — the session-expiry
             interceptor has redirected (T-25). -->
        <p class="branches__status branches__status--error" role="alert">
          The branch could not be deleted. Try again.
        </p>
      }

      @if (deletedName(); as name) {
        <p class="branches__status branches__deleted" role="status">
          Branch “{{ name }}” was deleted.
        </p>
      }

      @if (pendingDelete(); as branch) {
        <app-branch-delete-confirm
          [branchName]="branch.name"
          [deleting]="deleting()"
          (confirmed)="confirmDelete(branch)"
          (cancelled)="cancelDelete()"
        />
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

    .branches__delete {
      background: none;
      border: none;
      cursor: pointer;
      padding: 0;
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

  /**
   * The branch whose deletion is being confirmed, or null when no confirmation is open. Holding the
   * branch (not just an id) lets the dialog name it without a second lookup.
   */
  protected readonly pendingDelete = signal<Branch | null>(null);

  /** True only while a delete request is in flight — the guard against a double deletion. */
  protected readonly deleting = signal(false);
  protected readonly deleteFailed = signal(false);

  /** The name of the branch just deleted, shown as confirmation feedback until the next action. */
  protected readonly deletedName = signal<string | null>(null);

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

  /**
   * Opens the confirmation for a branch. This issues no request — deletion is destructive and
   * irreversible, so the first click only asks (FS-03 §6.3, AC-13). Any stale feedback from a
   * previous attempt is cleared so it cannot sit above a fresh confirmation.
   */
  protected startDelete(branch: Branch): void {
    this.deleteFailed.set(false);
    this.deletedName.set(null);
    this.pendingDelete.set(branch);
  }

  /**
   * Abandons a pending deletion. Nothing was sent when the confirmation opened and nothing is sent
   * now: the branch stays exactly as it was (FS-03 §6.3, AC-13).
   */
  protected cancelDelete(): void {
    if (this.deleting()) {
      return;
    }

    this.pendingDelete.set(null);
  }

  protected confirmDelete(branch: Branch): void {
    // A double-confirm must not issue two deletes: the second would 404 against an already-removed
    // branch, but the guard keeps the intent single regardless.
    if (this.deleting()) {
      return;
    }

    this.deleting.set(true);
    this.deleteFailed.set(false);

    this.branchService.delete(branch.branchId).subscribe({
      next: (result) => {
        this.deleting.set(false);
        this.pendingDelete.set(null);

        // Both a confirmed delete (void) and the endpoint's documented 404 (null) mean the branch is
        // gone: drop it from the list so it no longer appears, without a refetch. `result` carries
        // nothing about what was removed — no credential, no id (FS-03 §7.3).
        this.branches.update((branches) => branches.filter((b) => b.branchId !== branch.branchId));
        this.deletedName.set(branch.name);
        void result;
      },
      // Anything else — a 500, a dropped connection, or a 401 the session-expiry interceptor has
      // already acted on (T-25) — settles into the generic failure state with the branch untouched.
      error: () => {
        this.deleting.set(false);
        this.pendingDelete.set(null);
        this.deleteFailed.set(true);
      },
    });
  }
}
