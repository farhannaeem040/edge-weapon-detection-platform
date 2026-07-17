import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Branch } from './branch.models';
import { BranchService } from './branch.service';
import { branchDetailRoute } from './branch.routes';

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
  imports: [RouterLink],
  template: `
    <section class="branches">
      <header class="branches__header">
        <h2>Branches</h2>
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
              <span class="branches__status-badge">{{ branch.device.activationStatus }}</span>
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
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchListComponent implements OnInit {
  private readonly branchService = inject(BranchService);

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
}
