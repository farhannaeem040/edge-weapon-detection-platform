import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';

import { Branch } from '../branches/branch.models';
import { BranchService } from '../branches/branch.service';
import { LiveMonitoringComponent } from '../branches/live-monitoring';

/**
 * The global Monitoring page (FS-14 §5, IP-16 UI enhancement) — a thin Branch-selection wrapper
 * reachable from the main sidebar, reusing the exact same `LiveMonitoringComponent` the Branch
 * detail page's "Live Monitoring" tab already hosts. No player/WebRTC/WHEP/camera-switch/
 * mode-switch/cleanup code is duplicated here — all of that lives in `LiveMonitoringComponent`
 * (`branches/live-monitoring.ts`), unchanged, and is driven entirely by the `branchId` input this
 * page passes it once a Branch is selected. The Branch detail page's own tab keeps working exactly
 * as before — this is a second entry point onto the same underlying feature, not a second
 * implementation of it (FS-14 §5, "no second inference pipeline / no duplicated player").
 *
 * Branch selection mirrors `alert-list.ts`'s branch `<select>` pattern: `(change)` + per-option
 * `[selected]`, not `[(ngModel)]`, since the options themselves load asynchronously.
 */
@Component({
  selector: 'app-global-monitoring',
  imports: [LiveMonitoringComponent],
  template: `
    <section class="global-monitoring">
      <header class="global-monitoring__header page-header">
        <h2 class="page-header__title">Monitoring</h2>
      </header>

      @if (loading()) {
        <div class="card">
          <p class="global-monitoring__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading branches…
          </p>
        </div>
      } @else if (failed()) {
        <div class="card">
          <p class="global-monitoring__status banner banner--error card__body" role="alert">
            Branches could not be loaded. Try again.
          </p>
        </div>
      } @else if (branches().length === 0) {
        <div class="card">
          <p class="global-monitoring__status status-text card__body">
            No branches are available for monitoring.
          </p>
        </div>
      } @else {
        @if (branches().length > 1) {
          <label class="field global-monitoring__branch-field">
            <span class="field__label">Branch</span>
            <select class="global-monitoring__branch-select" (change)="onBranchChange($event)">
              @for (branch of branches(); track branch.branchId) {
                <option [value]="branch.branchId" [selected]="branch.branchId === selectedBranchId()">
                  {{ branch.name }}
                </option>
              }
            </select>
          </label>
        } @else {
          <p class="global-monitoring__single-branch status-text">{{ branches()[0].name }}</p>
        }

        <!--
          @for with track on the Branch id itself — not @if — is deliberate: LiveMonitoringComponent
          only loads its Cameras once, in ngOnInit, from the branchId input's value at creation time
          (exactly like the Branch detail tab, where branchId likewise never changes under one
          component instance). A plain @if would just patch the input on an existing instance when
          the Branch changes, silently leaving the previous Branch's Cameras/player in place. @for's
          identity tracking forces Angular to destroy the old instance (running its ngOnDestroy /
          player cleanup) and create a fresh one — a singleton array is the standard Angular idiom
          for "recreate this component when this one value's identity changes."
        -->
        @for (branchId of [selectedBranchId()]; track branchId) {
          @if (branchId) {
            <app-live-monitoring [branchId]="branchId" />
          }
        }
      }
    </section>
  `,
  styles: `
    .global-monitoring__branch-field {
      max-width: 20rem;
      margin-bottom: var(--space-4);
    }

    .global-monitoring__single-branch {
      margin: 0 0 var(--space-4);
      font-weight: var(--weight-medium);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GlobalMonitoringComponent implements OnInit {
  private readonly branchService = inject(BranchService);

  protected readonly branches = signal<Branch[]>([]);
  protected readonly loading = signal(true);
  protected readonly failed = signal(false);
  protected readonly selectedBranchId = signal<string | null>(null);

  ngOnInit(): void {
    this.branchService.list().subscribe({
      next: (branches) => {
        this.loading.set(false);
        this.branches.set(branches);
        if (branches.length > 0) {
          this.selectedBranchId.set(branches[0].branchId);
        }
      },
      error: () => {
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }

  protected onBranchChange(event: Event): void {
    const branchId = (event.target as HTMLSelectElement).value;
    // The template's @for(track branchId) is what turns this signal write into a full destroy of
    // the previous LiveMonitoringComponent instance (running its ngOnDestroy → stopPlayback
    // cleanup) and creation of a fresh one for the new Branch — see the template comment.
    this.selectedBranchId.set(branchId);
  }
}
