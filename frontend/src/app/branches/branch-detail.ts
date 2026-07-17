import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { Branch } from './branch.models';
import { BranchService } from './branch.service';
import { BRANCHES_ROUTE, BRANCH_ID_PARAM } from './branch.routes';

/**
 * One branch in full: its details, its configured cameras, and its Device's activation state
 * (IP-01 T-26; FS-02 §10.3, AC-1, AC-7).
 *
 * Three things this view deliberately does not do:
 *
 *  - It renders no Activation Key. The read endpoints do not return one and `Branch` does not model
 *    one, so there is nothing to display and nothing held in memory (FS-02 §5.4). Key display and
 *    regeneration are T-27/T-28.
 *  - It does not redact the RTSP URL. The Backend already did (`RtspUrlSanitizer`); the value bound
 *    below is exactly what arrived.
 *  - It does not infer activation from the presence of `deviceId`. `activationStatus` is the
 *    explicit field and the only one branched on.
 */
@Component({
  selector: 'app-branch-detail',
  imports: [RouterLink],
  template: `
    <section class="branch">
      <header class="branch__header">
        <h2>{{ branch()?.name ?? 'Branch' }}</h2>
        <a class="branch__back" [routerLink]="branchesRoute">Back to branches</a>
      </header>

      @if (loading()) {
        <p class="branch__status">Loading branch…</p>
      } @else if (notFound()) {
        <p class="branch__status" role="alert">That branch was not found.</p>
      } @else if (failed()) {
        <!-- Generic by design; no Backend error detail is surfaced (FS-02 §11). -->
        <p class="branch__status branch__status--error" role="alert">
          The branch could not be loaded. Try again.
        </p>
      } @else if (branch(); as branch) {
        <dl class="branch__fields">
          <dt>Address</dt>
          <dd class="branch__address">{{ branch.address }}</dd>
          <dt>Contact details</dt>
          <dd class="branch__contact">{{ branch.contactDetails }}</dd>
        </dl>

        <section class="branch__device">
          <h3>Device</h3>
          <p class="branch__device-status">{{ branch.device.activationStatus }}</p>

          @if (branch.device.activationStatus === 'Activated' && branch.device.deviceId) {
            <p class="branch__device-id">Device ID: {{ branch.device.deviceId }}</p>
          } @else {
            <!-- No placeholder, no blank field, no fabricated identifier: an unactivated Device has
                 no Device ID yet, and saying so is the honest rendering (FS-02 §10.3, AC-7). -->
            <p class="branch__device-id-absent">Device ID: not yet assigned</p>
          }
        </section>

        <section class="branch__cameras">
          <h3>Cameras</h3>
          @if (branch.cameras.length === 0) {
            <p class="branch__status">No cameras are configured for this branch.</p>
          } @else {
            <ul class="branch__camera-list">
              @for (camera of branch.cameras; track camera.cameraId) {
                <li class="branch__camera">
                  <span class="branch__camera-name">{{ camera.name }}</span>
                  <span class="branch__camera-url">{{ camera.rtspUrl }}</span>
                  <span class="branch__camera-enabled">{{
                    camera.enabled ? 'Enabled' : 'Disabled'
                  }}</span>
                </li>
              }
            </ul>
          }
        </section>
      }
    </section>
  `,
  styles: `
    .branch__header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 1rem;
    }

    .branch__camera-list {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .branch__camera {
      display: flex;
      align-items: baseline;
      gap: 1rem;
      padding: 0.5rem 0;
    }

    .branch__camera-url {
      flex: 1;
      font-family: monospace;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchDetailComponent implements OnInit {
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);

  protected readonly branchesRoute = BRANCHES_ROUTE;

  protected readonly branch = signal<Branch | null>(null);
  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly failed = signal(false);

  ngOnInit(): void {
    const branchId = this.route.snapshot.paramMap.get(BRANCH_ID_PARAM);

    if (!branchId) {
      // Unreachable through the router (the parameter is part of the path), but a missing id is a
      // not-found, never a request to the Backend for `/branches/`.
      this.settleAsNotFound();
      return;
    }

    this.branchService.get(branchId).subscribe({
      next: (branch) => {
        this.loading.set(false);

        if (branch === null) {
          this.notFound.set(true);
          return;
        }

        this.branch.set(branch);
      },
      // Anything other than the endpoint's documented 404 — including a 401 the session-expiry
      // interceptor has already acted on (T-25) — settles into the generic failure state.
      error: () => {
        this.loading.set(false);
        this.failed.set(true);
      },
    });
  }

  private settleAsNotFound(): void {
    this.loading.set(false);
    this.notFound.set(true);
  }
}
