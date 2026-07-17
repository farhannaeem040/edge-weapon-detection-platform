import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { ActivationKeyDisplayComponent } from './activation-key-display';
import { Branch } from './branch.models';
import { BranchService } from './branch.service';
import { BRANCHES_ROUTE, BRANCH_ID_PARAM } from './branch.routes';
import { DeviceStatusBadgeComponent } from './device-status-badge';

/**
 * One branch in full: its details, its configured cameras, its Device's activation state (IP-01
 * T-26; FS-02 §10.3, AC-1, AC-7), and the Admin's action to regenerate its Activation Key (IP-01
 * T-28; FS-02 §5.3, §10.2, AC-5).
 *
 * Three things the *read* side of this view deliberately does not do:
 *
 *  - It renders no Activation Key from the branch it loaded. The read endpoints do not return one and
 *    `Branch` does not model one (FS-02 §5.4). The only key this component can ever hold is one
 *    regeneration just minted, and it arrives from that response alone — never from a read.
 *  - It does not redact the RTSP URL. The Backend already did (`RtspUrlSanitizer`); the value bound
 *    below is exactly what arrived.
 *  - It does not infer activation from the presence of `deviceId`. `activationStatus` is the
 *    explicit field and the only one branched on.
 *
 * The regeneration flow is three phases — action, confirmation, disclosure — and the shape follows
 * from what regeneration is. It is destructive: the previous key is invalidated whether or not it was
 * ever used (FS-02 §5.3 step 3), so it never happens on a single click, and cancelling sends nothing.
 * The response is the only moment the new plaintext key exists (the Backend keeps only a hash and
 * cannot re-derive it — FS-02 §1.4), so success must not navigate away; the Admin leaves explicitly,
 * via "Done", once they hold it.
 *
 * The key lives in one in-memory signal and nowhere else — no storage, no cookie, no URL, no query
 * or route parameter, no router state, no log. It is cleared when the flow completes and when the
 * component is destroyed, so navigating away or refreshing re-runs a plain read that recovers
 * nothing.
 */
@Component({
  selector: 'app-branch-detail',
  imports: [RouterLink, ActivationKeyDisplayComponent, DeviceStatusBadgeComponent],
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
          <app-device-status-badge
            class="branch__device-status"
            [status]="branch.device.activationStatus"
          />

          @if (branch.device.activationStatus === 'Activated' && branch.device.deviceId) {
            <p class="branch__device-id">Device ID: {{ branch.device.deviceId }}</p>
          } @else {
            <!-- No placeholder, no blank field, no fabricated identifier: an unactivated Device has
                 no Device ID yet, and saying so is the honest rendering (FS-02 §10.3, AC-7). -->
            <p class="branch__device-id-absent">Device ID: not yet assigned</p>
          }

          <!-- The action is offered in both Device states. FS-02 §5.3/FR-BRN-005 restrict it to
               neither: regeneration invalidates the current key "regardless of its consumption
               state", which is precisely the activated case (§15 T-09, reactivation). -->
          <section class="branch__regeneration">
            @if (regeneratedKey(); as key) {
              <app-activation-key-display
                [activationKey]="key"
                heading="New Activation Key"
                [regenerated]="true"
                continueLabel="Done"
                (continued)="completeRegeneration()"
              />
            } @else if (confirming()) {
              <div
                class="branch__confirm"
                role="group"
                aria-labelledby="regenerate-confirm-heading"
              >
                <h4 id="regenerate-confirm-heading">Regenerate this branch's Activation Key?</h4>

                <ul class="branch__confirm-effects">
                  <li>The current Activation Key stops working immediately and cannot be restored.</li>
                  <li>A new Activation Key is generated and shown to you once.</li>
                  <li>The branch's public Device ID does not change.</li>
                  <li>An already activated Device is not deactivated and keeps running.</li>
                  <li>The new key is required for the next activation or reactivation.</li>
                </ul>

                <button
                  class="branch__confirm-cancel"
                  type="button"
                  [disabled]="regenerating()"
                  (click)="cancelRegeneration()"
                >
                  Cancel
                </button>

                <!-- Disabled while in flight: a second click must not invalidate the key the first
                     one just minted, before the Admin has even seen it. -->
                <button
                  class="branch__confirm-regenerate"
                  type="button"
                  [disabled]="regenerating()"
                  (click)="regenerate()"
                >
                  {{ regenerating() ? 'Regenerating…' : 'Regenerate key' }}
                </button>
              </div>
            } @else {
              <button class="branch__regenerate" type="button" (click)="startRegeneration()">
                Regenerate Activation Key
              </button>

              @if (regenerateNotFound()) {
                <p class="branch__regenerate-status" role="alert">
                  This branch's Device was not found. It may have been removed.
                </p>
              } @else if (regenerateFailed()) {
                <!-- One fixed message for every failure mode. No Backend text, no status code, no
                     key material — and no stale key: the disclosure above renders only from a
                     successful response (FS-02 §11). A 401 never reaches here; the session-expiry
                     interceptor has already redirected (T-25). -->
                <p
                  class="branch__regenerate-status branch__regenerate-status--error"
                  role="alert"
                >
                  The Activation Key could not be regenerated. Try again.
                </p>
              }
            }
          </section>
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

    /* The badge replaced a <p> here; keep the block flow that surrounded it. */
    .branch__device-status {
      display: block;
      margin: 0.5rem 0;
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
export class BranchDetailComponent implements OnInit, OnDestroy {
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);

  protected readonly branchesRoute = BRANCHES_ROUTE;

  protected readonly branch = signal<Branch | null>(null);
  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly failed = signal(false);

  /** True once the Admin has asked to regenerate and before they confirm or cancel. */
  protected readonly confirming = signal(false);

  /** True only while a regenerate request is in flight — the guard against a double submission. */
  protected readonly regenerating = signal(false);

  protected readonly regenerateFailed = signal(false);
  protected readonly regenerateNotFound = signal(false);

  /**
   * The new complete plaintext Activation Key, held from the regenerate response until the Admin
   * presses "Done". This signal is the key's only residence anywhere in the browser.
   */
  protected readonly regeneratedKey = signal<string | null>(null);

  /** The branch this view loaded, and the identifier regeneration addresses the Device by. */
  private branchId: string | null = null;

  ngOnInit(): void {
    const branchId = this.route.snapshot.paramMap.get(BRANCH_ID_PARAM);
    this.branchId = branchId;

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

  ngOnDestroy(): void {
    // Belt and braces: the signal would be garbage anyway once the component goes, but the key's
    // lifetime is a stated requirement, so it is ended explicitly rather than left to the collector.
    this.regeneratedKey.set(null);
  }

  /**
   * Opens the confirmation. This issues no request — regeneration is destructive, so the Admin must
   * say so twice, and the first click only asks.
   */
  protected startRegeneration(): void {
    // Clears the previous attempt's message: the Admin is trying again, and a stale error above a
    // fresh confirmation would be reporting on something that is no longer happening.
    this.regenerateFailed.set(false);
    this.regenerateNotFound.set(false);
    this.confirming.set(true);
  }

  /**
   * Abandons the regeneration. Nothing was sent when the confirmation opened and nothing is sent now:
   * the current key stays valid, the branch stays exactly as it was, and no key state ever existed.
   */
  protected cancelRegeneration(): void {
    if (this.regenerating()) {
      return;
    }

    this.confirming.set(false);
  }

  protected regenerate(): void {
    // A double-click must not regenerate twice: the second response would invalidate the first key
    // before the Admin could read it, and that key can never be recovered.
    if (this.regenerating() || this.branchId === null) {
      return;
    }

    this.regenerating.set(true);
    this.regenerateFailed.set(false);
    this.regenerateNotFound.set(false);

    // The branch id, which is what this endpoint's `{id}` means (FS-02 §1.3) — see
    // `BranchService.regenerateActivationKey`.
    this.branchService.regenerateActivationKey(this.branchId).subscribe({
      next: (activationKey) => {
        this.regenerating.set(false);

        if (activationKey === null) {
          // The endpoint's documented 404 (FS-02 §13). No key was minted, so none is shown.
          this.confirming.set(false);
          this.regenerateNotFound.set(true);
          return;
        }

        // The confirmation is replaced by the disclosure. Navigation waits for the Admin
        // (FS-02 §5.3 step 6).
        this.confirming.set(false);
        this.regeneratedKey.set(activationKey);
      },
      // Anything else — including a 401 the session-expiry interceptor has already acted on
      // (T-25) — settles into the generic failure state, with no key displayed.
      error: () => {
        this.regenerating.set(false);
        this.confirming.set(false);
        this.regenerateFailed.set(true);
      },
    });
  }

  /**
   * Ends the disclosure once the Admin says they have the key, discarding it and returning to the
   * ordinary branch view.
   *
   * The branch is deliberately not re-read. Regeneration changes no field this view renders: the
   * Device's `activationStatus` is untouched and its `DeviceId` is retained (FS-02 §5.3, AC-7), so a
   * refetch would only re-render identical data. Nothing about leaving this state carries the key —
   * there is no navigation here at all.
   */
  protected completeRegeneration(): void {
    this.regeneratedKey.set(null);
  }

  private settleAsNotFound(): void {
    this.loading.set(false);
    this.notFound.set(true);
  }
}
