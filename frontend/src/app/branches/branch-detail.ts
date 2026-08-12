import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ActivationKeyDisplayComponent } from './activation-key-display';
import { Branch, DEFAULT_RTSP_OUTPUT_PORT } from './branch.models';
import { ActivationKeyRegenerationConflictError, BranchService } from './branch.service';
import { BranchDeleteConfirmComponent } from './branch-delete-confirm';
import { BRANCHES_ROUTE, BRANCH_ID_PARAM, branchEditRoute } from './branch.routes';
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
 *    regeneration just minted, and it arrives from that response alone — never from a read. There is
 *    no persistent or masked key display anywhere in this view.
 *  - It does not redact the RTSP URL. The Backend already did (`RtspUrlSanitizer`); the value bound
 *    below is exactly what arrived.
 *  - It does not infer activation from the presence of `deviceId`. `activationStatus` is the
 *    explicit field and the only one branched on.
 *
 * It renders only fields the API returns: name, address, contact details, device activation status,
 * the public Device ID once activated, and cameras (name, RTSP, enabled). No Site Manager, phone/email
 * split, timezone, gateway id, heartbeat, latency, or camera IP/resolution/live-state — none exist in
 * the contract (SCREEN-INVENTORY.md), and no internal identifier or secret is ever shown.
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
  imports: [
    RouterLink,
    ActivationKeyDisplayComponent,
    DeviceStatusBadgeComponent,
    BranchDeleteConfirmComponent,
  ],
  template: `
    <section class="branch">
      <header class="branch__header page-header">
        <div class="page-header__titles">
          <a class="branch__back breadcrumb" [routerLink]="branchesRoute">← Branches</a>
          <h2 class="page-header__title">{{ branch()?.name ?? 'Branch' }}</h2>
        </div>

        @if (branch(); as branch) {
          <!-- Edit/Delete actions for this branch (FS-03 §6.2, AC-15). The Delete action is added by
               T-46; Edit is the link here. It names the branch in both its aria-label and its title,
               so it reads meaningfully to assistive tech and on hover (FS-03 §8, AC-16). -->
          <div class="branch__actions page-header__actions">
            <a
              class="branch__edit btn btn--secondary"
              [routerLink]="editRoute()"
              [attr.aria-label]="'Edit branch ' + branch.name"
              [title]="'Edit branch ' + branch.name"
            >
              <svg
                class="branch__action-icon"
                viewBox="0 0 24 24"
                width="18"
                height="18"
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
              <span>Edit branch</span>
            </a>

            <!-- Delete is a button, not a link, and never the branch name: it only opens the
                 confirmation (below), from which the delete request is issued (FS-03 §6.2, §6.3). -->
            <button
              class="branch__delete btn btn--danger"
              type="button"
              [attr.aria-label]="'Delete branch ' + branch.name"
              [title]="'Delete branch ' + branch.name"
              (click)="startDelete()"
            >
              <svg
                class="branch__action-icon"
                viewBox="0 0 24 24"
                width="18"
                height="18"
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
              <span>Delete branch</span>
            </button>
          </div>
        }
      </header>

      @if (loading()) {
        <div class="card">
          <p class="branch__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading branch…
          </p>
        </div>
      } @else if (notFound()) {
        <div class="card">
          <p class="branch__status card__body banner banner--info" role="alert">
            That branch was not found.
          </p>
        </div>
      } @else if (failed()) {
        <!-- Generic by design; no Backend error detail is surfaced (FS-02 §11). -->
        <div class="card">
          <p class="branch__status branch__status--error card__body banner banner--error" role="alert">
            The branch could not be loaded. Try again.
          </p>
        </div>
      } @else if (branch(); as branch) {
        @if (deleteFailed()) {
          <!-- Generic; no Backend text or status code (FS-03 §12). A 401 never reaches here. -->
          <p class="branch__status branch__status--error banner banner--error" role="alert">
            The branch could not be deleted. Try again.
          </p>
        }

        @if (confirmingDelete()) {
          <app-branch-delete-confirm
            [branchName]="branch.name"
            [deleting]="deleting()"
            (confirmed)="confirmDelete()"
            (cancelled)="cancelDelete()"
          />
        }

        <div class="branch__grid">
          <section class="branch__card card">
            <header class="card__header"><h3>Branch information</h3></header>
            <dl class="branch__fields card__body">
              <dt>Address</dt>
              <dd class="branch__address">{{ branch.address }}</dd>
              <dt>Contact details</dt>
              <dd class="branch__contact">{{ branch.contactDetails }}</dd>
            </dl>
          </section>

          <section class="branch__device branch__card card">
            <header class="card__header"><h3>Device</h3></header>
            <div class="card__body">
              <app-device-status-badge
                class="branch__device-status"
                [status]="branch.device.activationStatus"
              />

              @if (
                (branch.device.activationStatus === 'Activated' ||
                  branch.device.activationStatus === 'ReactivationRequired') &&
                branch.device.deviceId
              ) {
                <!-- The Device ID is shown once a Device has one. It survives a credential
                     revocation (FS-02 §4.2): a ReactivationRequired Device keeps the same permanent
                     Device ID, so it is still displayed here. Status is trusted, not deviceId's mere
                     presence, so a self-contradictory payload never over-reports. -->
                <p class="branch__device-id">Device ID: {{ branch.device.deviceId }}</p>
              } @else {
                <!-- No placeholder, no blank field, no fabricated identifier: an unactivated Device has
                     no Device ID yet, and saying so is the honest rendering (FS-02 §10.3, AC-7). -->
                <p class="branch__device-id-absent">Device ID: not yet assigned</p>
              }

              <!--
                FS-12 §4 — the Jetson's network location. Displayed as two independent facts plus the
                Backend-computed base, rather than a URL the browser assembles: composing it here
                would mean re-implementing IPv6 bracketing and the legacy-column fallback in the
                client. Precedence is the Backend's value, then an explicit "not configured" — never
                a guessed address (task Phase 10).
              -->
              @if (branch.device.jetsonHost) {
                <p class="branch__device-jetson-host">Jetson host: {{ branch.device.jetsonHost }}</p>
                <p class="branch__device-rtsp-port">
                  RTSP output port: {{ branch.device.rtspOutputPort ?? defaultRtspOutputPort }}
                </p>
              } @else {
                <p class="branch__device-jetson-host-absent status-text">
                  Jetson output network is not configured
                </p>
              }

              @if (branch.device.annotatedOutputBaseUrl) {
                <p class="branch__device-output-base">
                  Annotated output base: {{ branch.device.annotatedOutputBaseUrl }}
                </p>
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
                    [class.branch__confirm--destructive]="regenerationIsDestructive()"
                    role="group"
                    aria-labelledby="regenerate-confirm-heading"
                  >
                    <h4 id="regenerate-confirm-heading">Regenerate this branch's Activation Key?</h4>

                    @if (regenerationIsDestructive()) {
                      <!-- Activated or ReactivationRequired: regeneration is a security-first credential
                           reset (IP-05 §1). The warning states exactly what it does — revoke now, lock
                           the running Jetson once it detects the revocation, require a manual
                           reactivation, and preserve the Device ID — so the Admin confirms with full
                           knowledge (FS-02 §5.3, AC-8). -->
                      <p class="branch__confirm-warning" role="alert">
                        This is a destructive credential reset.
                      </p>
                      <ul class="branch__confirm-effects">
                        <li>The current device credential is revoked immediately and cannot be restored.</li>
                        <li>The running Jetson will lock itself once it detects the revocation.</li>
                        <li>The replacement Activation Key must be provisioned on the Jetson manually.</li>
                        <li>The branch's public Device ID is preserved.</li>
                        <li>The new Activation Key is shown to you once and cannot be retrieved again.</li>
                      </ul>
                    } @else {
                      <!-- Unactivated: there is no live credential and no running Jetson to revoke, so
                           this is the ordinary first-activation key-generation flow, with no
                           destructive credential-revocation warning (IP-05 P1, FS-02 §5.3). -->
                      <ul class="branch__confirm-effects">
                        <li>This branch's Device has not been activated, so no running device is affected.</li>
                        <li>The current unused Activation Key stops working immediately and is replaced.</li>
                        <li>The branch's public Device ID is unaffected.</li>
                        <li>The new Activation Key is shown to you once and cannot be retrieved again.</li>
                      </ul>
                    }

                    <div class="branch__confirm-actions">
                      <button
                        class="branch__confirm-cancel btn btn--ghost"
                        type="button"
                        [disabled]="regenerating()"
                        (click)="cancelRegeneration()"
                      >
                        Cancel
                      </button>

                      <!-- Disabled while in flight: a second click must not invalidate the key the
                           first one just minted, before the Admin has even seen it. -->
                      <button
                        class="branch__confirm-regenerate btn"
                        [class.btn--danger]="regenerationIsDestructive()"
                        [class.btn--primary]="!regenerationIsDestructive()"
                        type="button"
                        [disabled]="regenerating()"
                        (click)="regenerate()"
                      >
                        {{
                          regenerating()
                            ? 'Regenerating…'
                            : regenerationIsDestructive()
                              ? 'Regenerate key'
                              : 'Generate new key'
                        }}
                      </button>
                    </div>
                  </div>
                } @else {
                  <button class="branch__regenerate btn btn--secondary" type="button" (click)="startRegeneration()">
                    Regenerate Activation Key
                  </button>

                  @if (regenerateConflict()) {
                    <!-- The Backend answered 409 ACTIVATION_KEY_REGENERATION_CONFLICT (IP-05 §3): a
                         concurrent regeneration won the race and this request committed no key. No key
                         is shown (there is none), and the message makes no claim about whether the
                         competing request succeeded — the refreshed state above is the source of
                         truth, and the Admin may simply try again if they still need a new key. -->
                    <p class="branch__regenerate-status banner banner--warning" role="alert">
                      Another regeneration for this branch was completed at the same time, so no key
                      was issued to you. Review the branch's current state above and try again if you
                      still need a new Activation Key.
                    </p>
                  } @else if (regenerateNotFound()) {
                    <p class="branch__regenerate-status banner banner--error" role="alert">
                      This branch's Device was not found. It may have been removed.
                    </p>
                  } @else if (regenerateFailed()) {
                    <!-- One fixed message for every failure mode. No Backend text, no status code, no
                         key material — and no stale key: the disclosure above renders only from a
                         successful response (FS-02 §11). A 401 never reaches here; the session-expiry
                         interceptor has already redirected (T-25). -->
                    <p
                      class="branch__regenerate-status branch__regenerate-status--error banner banner--error"
                      role="alert"
                    >
                      The Activation Key could not be regenerated. Try again.
                    </p>
                  }
                }
              </section>
            </div>
          </section>
        </div>

        <section class="branch__cameras branch__card card">
          <header class="card__header"><h3>Connected cameras</h3></header>
          <div class="card__body">
            @if (branch.cameras.length === 0) {
              <p class="branch__status status-text">No cameras are configured for this branch.</p>
            } @else {
              <ul class="branch__camera-list">
                @for (camera of branch.cameras; track camera.cameraId) {
                  <li class="branch__camera">
                    <div class="branch__camera-primary">
                      <span class="branch__camera-name">
                        <span class="branch__camera-field-label">Camera name:</span>
                        {{ camera.name }}
                      </span>
                      <!--
                        FS-12 §2: the *public* stream identifier — what an operator reads and types.
                        The immutable cameraId is deliberately not shown as the primary identifier;
                        it stays available below, explicitly labelled as internal.
                      -->
                      <span class="branch__camera-key">
                        <span class="branch__camera-field-label">Camera key:</span>
                        {{ camera.cameraKey }}
                      </span>
                      <span class="branch__camera-source-order">
                        <span class="branch__camera-field-label">Source order:</span>
                        {{ camera.sourceOrder }}
                      </span>
                      <span class="branch__camera-url">
                        <span class="branch__camera-field-label">Input stream URL:</span>
                        {{ camera.rtspUrl }}
                      </span>
                    </div>

                    <span class="branch__camera-status">
                      <span class="branch__camera-field-label">Status:</span>
                      <span
                        class="branch__camera-enabled badge"
                        [class.branch__camera-enabled--on]="camera.enabled"
                        [class.branch__camera-enabled--off]="!camera.enabled"
                        >{{ camera.enabled ? 'Enabled' : 'Disabled' }}</span
                      >
                    </span>

                    <!--
                      FS-11 §11: the annotated output is a *different* stream from the input above —
                      the same camera after inference, with detection boxes drawn on it. Shown
                      separately and labelled so the two can never be confused. Null base URL is
                      rendered as an explicit "not configured" state, never a guessed address.
                    -->
                    <span class="branch__camera-output">
                      <span class="branch__camera-field-label">Annotated output URL:</span>
                      @if (camera.outputStreamUrl) {
                        {{ camera.outputStreamUrl }}
                      } @else {
                        <span class="branch__camera-output-missing status-text">
                          Output base URL not configured
                        </span>
                      }
                    </span>

                    @if (clipboardAvailable) {
                      <button
                        class="branch__camera-copy btn btn--secondary"
                        type="button"
                        (click)="copyCameraStreamUrl(camera.cameraId, camera.rtspUrl)"
                      >
                        Copy input URL
                      </button>
                      @if (camera.outputStreamUrl) {
                        <button
                          class="branch__camera-copy-output btn btn--secondary"
                          type="button"
                          (click)="copyCameraOutputUrl(camera.cameraId, camera.outputStreamUrl)"
                        >
                          Copy output URL
                        </button>
                      }
                    } @else {
                      <p class="branch__camera-copy-unavailable status-text">
                        Copying is unavailable in this browser. Select the URL above and copy it manually.
                      </p>
                    }

                    @if (cameraCopyState(camera.cameraId) === 'copied') {
                      <p class="branch__camera-copy-status status-text" role="status">
                        Stream URL copied to the clipboard.
                      </p>
                    } @else if (cameraCopyState(camera.cameraId) === 'failed') {
                      <p class="branch__camera-copy-status status-text--error" role="alert">
                        The URL could not be copied. Select it above and copy it manually.
                      </p>
                    }
                  </li>
                }
              </ul>
            }
          </div>
        </section>
      }
    </section>
  `,
  styles: `
    .branch__actions {
      display: flex;
      align-items: center;
      gap: var(--space-2);
    }

    .branch__edit,
    .branch__delete {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
    }

    .branch__grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
      gap: var(--space-5);
      margin-bottom: var(--space-5);
    }

    /* The badge replaced a <p> here; keep the block flow that surrounded it. */
    .branch__device-status {
      display: block;
      margin: 0 0 var(--space-3);
    }

    .branch__device-id,
    .branch__device-id-absent {
      font-size: var(--text-sm);
      color: var(--color-text-muted);
      margin: 0 0 var(--space-4);
    }

    .branch__fields {
      display: grid;
      grid-template-columns: auto 1fr;
      gap: var(--space-2) var(--space-5);
      margin: 0;
    }

    .branch__fields dt {
      font-family: var(--font-heading);
      font-size: var(--text-label);
      font-weight: var(--weight-medium);
      color: var(--color-text-faint);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }

    .branch__fields dd {
      margin: 0;
      color: var(--color-text);
    }

    .branch__confirm {
      border: 1px solid var(--color-border);
      border-radius: var(--radius);
      background: var(--color-surface-subtle);
      padding: var(--space-4);
    }

    .branch__confirm h4 {
      margin: 0 0 var(--space-2);
    }

    /* A destructive reset is bordered and headed in the danger colour so it does not read as the
       same benign generate-a-new-key prompt an unactivated branch shows. */
    .branch__confirm--destructive {
      border-color: var(--color-danger, #ba1a1a);
    }

    .branch__confirm-warning {
      margin: 0 0 var(--space-2);
      font-weight: var(--weight-medium);
      color: var(--color-danger, #ba1a1a);
    }

    .branch__confirm-effects {
      margin: 0 0 var(--space-4);
      padding-left: 1.2rem;
      color: var(--color-text-muted);
      font-size: var(--text-sm);
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
    }

    .branch__confirm-actions {
      display: flex;
      gap: var(--space-2);
    }

    .branch__regenerate-status {
      margin-top: var(--space-3);
    }

    .branch__camera-field-label {
      font-weight: var(--weight-medium);
      color: var(--color-text-faint);
      margin-right: var(--space-1);
    }

    .branch__camera-list {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    .branch__camera {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-2) var(--space-4);
      padding: var(--space-3) 0;
      border-bottom: 1px solid var(--color-border);
    }

    .branch__camera:last-child {
      border-bottom: 0;
    }

    .branch__camera-primary {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      flex: 1 1 20rem;
      min-width: 0;
    }

    .branch__camera-name {
      font-weight: var(--weight-medium);
    }

    .branch__camera-url {
      font-family: var(--font-mono);
      font-size: var(--text-sm);
      color: var(--color-text-muted);
      overflow-wrap: anywhere;
      user-select: all;
    }

    .branch__camera-status {
      display: flex;
      align-items: center;
      gap: var(--space-1);
    }

    .branch__camera-enabled--on {
      background: var(--color-success-bg);
      color: var(--color-success);
      border-color: #b7ddc4;
    }

    .branch__camera-enabled--off {
      background: var(--color-neutral-bg);
      color: var(--color-neutral-text);
      border-color: var(--color-border);
    }

    .branch__camera-copy-status,
    .branch__camera-copy-unavailable {
      flex-basis: 100%;
      margin: 0;
    }

    @media (max-width: 640px) {
      .branch__camera {
        flex-direction: column;
        align-items: flex-start;
        gap: var(--space-2);
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchDetailComponent implements OnInit, OnDestroy {
  /** Mirrors the Backend default, shown when a Device stores a host but no explicit port. */
  protected readonly defaultRtspOutputPort = DEFAULT_RTSP_OUTPUT_PORT;

  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly branchesRoute = BRANCHES_ROUTE;

  protected readonly branch = signal<Branch | null>(null);
  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly failed = signal(false);

  /**
   * Whether the browser exposes `navigator.clipboard` (absent in insecure contexts and older
   * browsers) — read once, purely to decide whether to offer each camera's copy button (mirrors
   * `ActivationKeyDisplayComponent`'s identical check).
   */
  protected readonly clipboardAvailable =
    typeof navigator !== 'undefined' && navigator.clipboard !== undefined;

  /**
   * The outcome of the most recent "Copy URL" press, per camera (a branch can have more than one) —
   * `idle`/absent until that camera's button is pressed.
   */
  protected readonly cameraCopyStates = signal<ReadonlyMap<string, 'idle' | 'copied' | 'failed'>>(
    new Map(),
  );

  /** True once the Admin has asked to regenerate and before they confirm or cancel. */
  protected readonly confirming = signal(false);

  /** True only while a regenerate request is in flight — the guard against a double submission. */
  protected readonly regenerating = signal(false);

  protected readonly regenerateFailed = signal(false);
  protected readonly regenerateNotFound = signal(false);

  /**
   * True when the Backend answered 409 ACTIVATION_KEY_REGENERATION_CONFLICT (IP-05 §3): a concurrent
   * regeneration won the race and this request committed no key. Rendered as safe retry guidance,
   * separate from the generic failure and the not-found states, and never alongside a key.
   */
  protected readonly regenerateConflict = signal(false);

  /**
   * Whether regenerating this branch's key is a destructive credential reset — true only when the
   * Device is `Activated` or `ReactivationRequired`, i.e. there is a live (or last-issued) credential
   * to revoke and a Jetson that must be reactivated (FS-02 §5.3, AC-8). An `Unactivated` Device has
   * no live credential, so its regeneration is the benign first-activation flow with no destructive
   * warning. Derived from the loaded branch's explicit `activationStatus`, never from `deviceId`.
   */
  protected readonly regenerationIsDestructive = computed(() => {
    const status = this.branch()?.device.activationStatus;
    return status === 'Activated' || status === 'ReactivationRequired';
  });

  /** True once the Admin has asked to delete and before they confirm or cancel. */
  protected readonly confirmingDelete = signal(false);

  /** True only while a delete request is in flight — the guard against a double deletion. */
  protected readonly deleting = signal(false);
  protected readonly deleteFailed = signal(false);

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
    this.regenerateConflict.set(false);
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
    this.regenerateConflict.set(false);

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
        // (FS-02 §5.3 step 6). The branch/device is re-read so the status badge reflects the new
        // state — `ReactivationRequired` for a Device that had been Activated (FS-02 §4.1, IP-05 P1).
        this.confirming.set(false);
        this.regeneratedKey.set(activationKey);
        this.reloadBranch();
      },
      error: (error: unknown) => {
        this.regenerating.set(false);
        this.confirming.set(false);

        if (error instanceof ActivationKeyRegenerationConflictError) {
          // A lost concurrent-regeneration race (IP-05 §3): the Backend committed no key. Clear any
          // stale key, show safe retry guidance, and re-read the branch so the view reflects whatever
          // state the winning request left — without assuming it succeeded. No auto-retry.
          this.regeneratedKey.set(null);
          this.regenerateConflict.set(true);
          this.reloadBranch();
          return;
        }

        // Anything else — including a 401 the session-expiry interceptor has already acted on
        // (T-25) — settles into the generic failure state, with no key displayed. The key is cleared
        // explicitly so a stale disclosure from an earlier attempt can never survive a later failure.
        this.regeneratedKey.set(null);
        this.regenerateFailed.set(true);
      },
    });
  }

  /**
   * Re-reads the branch after a regeneration so the status badge and Device ID reflect the new state
   * (e.g. `Activated → ReactivationRequired`, FS-02 §4.1). It is best-effort: a failed refresh leaves
   * the currently displayed branch and any key disclosure in place rather than tearing them down, and
   * it never toggles the full-page loading state. This issues exactly one GET and no retry.
   */
  private reloadBranch(): void {
    if (this.branchId === null) {
      return;
    }

    this.branchService.get(this.branchId).subscribe({
      next: (branch) => {
        if (branch !== null) {
          this.branch.set(branch);
        }
      },
      // A failed refresh is not surfaced: the disclosed key must stay on screen, and the last known
      // branch is a safer thing to show than an error that would discard it.
      error: () => {},
    });
  }

  /**
   * Ends the disclosure once the Admin says they have the key, discarding it and returning to the
   * ordinary branch view.
   *
   * No re-read happens here: the branch was already refreshed the moment the regeneration succeeded
   * (see `regenerate`), so the status badge already shows the new state — `ReactivationRequired` for a
   * Device that had been Activated (FS-02 §4.1, IP-05 P1). Leaving the disclosure only drops the key
   * from memory; there is no navigation here, so nothing carries it anywhere.
   */
  protected completeRegeneration(): void {
    this.regeneratedKey.set(null);
  }

  /** The given camera's most recent copy outcome, or `'idle'` if its button has never been pressed. */
  protected cameraCopyState(cameraId: string): 'idle' | 'copied' | 'failed' {
    return this.cameraCopyStates().get(cameraId) ?? 'idle';
  }

  /**
   * Copies one camera's stream URL to the clipboard, in response to the Admin's click on that
   * camera's own button and never otherwise. This is operational configuration, not a secret, but
   * both outcomes are still reported on screen so a silent failure never leaves the Admin unsure
   * whether the copy worked (mirrors `ActivationKeyDisplayComponent.copy`).
   */
  protected copyCameraStreamUrl(cameraId: string, streamUrl: string): void {
    if (!this.clipboardAvailable) {
      return;
    }

    navigator.clipboard.writeText(streamUrl).then(
      () => this.setCameraCopyState(cameraId, 'copied'),
      () => this.setCameraCopyState(cameraId, 'failed'),
    );
  }

  /**
   * Copies the camera's *annotated output* URL (FS-11 §11) — deliberately a separate action from
   * the input-URL copy above so an Admin cannot copy one believing it is the other.
   */
  protected copyCameraOutputUrl(cameraId: string, outputStreamUrl: string): void {
    if (!this.clipboardAvailable) {
      return;
    }

    navigator.clipboard.writeText(outputStreamUrl).then(
      () => this.setCameraCopyState(cameraId, 'copied'),
      () => this.setCameraCopyState(cameraId, 'failed'),
    );
  }

  private setCameraCopyState(cameraId: string, state: 'copied' | 'failed'): void {
    this.cameraCopyStates.update((states) => {
      const next = new Map(states);
      next.set(cameraId, state);
      return next;
    });
  }

  /**
   * The edit route for the branch on screen. Falls back to the list route only for the unreachable
   * missing-id case (the action is rendered only inside a loaded branch, where the id is present).
   */
  protected editRoute(): string {
    return this.branchId ? branchEditRoute(this.branchId) : this.branchesRoute;
  }

  /**
   * Opens the delete confirmation. This issues no request — deletion is destructive and irreversible,
   * so the first click only asks (FS-03 §6.3, AC-13). Any stale failure is cleared first.
   */
  protected startDelete(): void {
    this.deleteFailed.set(false);
    this.confirmingDelete.set(true);
  }

  /**
   * Abandons the deletion. Nothing was sent when the confirmation opened and nothing is sent now: the
   * branch stays exactly as it was (FS-03 §6.3, AC-13).
   */
  protected cancelDelete(): void {
    if (this.deleting()) {
      return;
    }

    this.confirmingDelete.set(false);
  }

  protected confirmDelete(): void {
    // A double-confirm must not issue two deletes.
    if (this.deleting() || this.branchId === null) {
      return;
    }

    this.deleting.set(true);
    this.deleteFailed.set(false);

    this.branchService.delete(this.branchId).subscribe({
      // Both a confirmed delete (void) and the endpoint's documented 404 (null) mean the branch is
      // gone; either way the safe result is to leave for the list, where it no longer appears
      // (FS-03 §6.3, §10.2). No credential or id is carried anywhere in this navigation.
      next: () => {
        this.deleting.set(false);
        this.confirmingDelete.set(false);
        void this.router.navigateByUrl(BRANCHES_ROUTE);
      },
      // Anything else — a 500, a dropped connection, or a 401 the session-expiry interceptor has
      // already acted on (T-25) — settles into the generic failure state with the branch untouched.
      error: () => {
        this.deleting.set(false);
        this.confirmingDelete.set(false);
        this.deleteFailed.set(true);
      },
    });
  }

  private settleAsNotFound(): void {
    this.loading.set(false);
    this.notFound.set(true);
  }
}
