import { ChangeDetectionStrategy, Component, OnDestroy, inject, signal } from '@angular/core';
import { FormArray, FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ActivationKeyDisplayComponent } from './activation-key-display';
import { BranchService } from './branch.service';
import { CameraConfigFormComponent } from './camera-config-form';
import {
  BRANCH_ADDRESS_MAX_LENGTH,
  BRANCH_CONTACT_DETAILS_MAX_LENGTH,
  BRANCH_NAME_MAX_LENGTH,
  CAMERA_NAME_MAX_LENGTH,
  CAMERA_RTSP_URL_MAX_LENGTH,
  CreateBranchRequest,
} from './branch.models';
import { BRANCHES_ROUTE, branchDetailRoute } from './branch.routes';
import { notBlank, rtspUrl } from './branch.validators';

/**
 * Branch creation: the form, its cameras, and the one-time Activation Key disclosure that follows a
 * successful submission (IP-01 T-27; FS-02 §5.1, §10.1, AC-1, AC-2).
 *
 * The view is two phases, and the second is the reason for the shape of the first. Creating a branch
 * is the only moment the plaintext Activation Key exists (the Backend stores only a hash and cannot
 * re-derive it — FS-02 §1.4), so a successful submission must not navigate away: doing so would
 * destroy the key before the Admin could record it. Instead the form is replaced by the key, and the
 * Admin leaves explicitly once they have it.
 *
 * The key lives in one in-memory signal and nowhere else — no storage, no URL, no router state, no
 * log. It is cleared when the flow completes and when the component is destroyed, so a refresh
 * re-runs an empty form rather than recovering anything.
 */
@Component({
  selector: 'app-branch-create',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    CameraConfigFormComponent,
    ActivationKeyDisplayComponent,
  ],
  template: `
    <section class="branch-create branch-form">
      <header class="branch-create__header page-header">
        <div class="page-header__titles">
          <a class="branch-create__back breadcrumb" [routerLink]="branchesRoute">← Branches</a>
          <h2 class="page-header__title">Create branch</h2>
        </div>
      </header>

      @if (activationKey(); as key) {
        <div class="card">
          <div class="card__body">
            <p class="branch-create__created banner banner--info" role="status">Branch created.</p>
            <app-activation-key-display [activationKey]="key" (continued)="continueToBranch()" />
          </div>
        </div>
      } @else {
        <form class="branch-create__form branch-form__form" [formGroup]="form" (ngSubmit)="submit()">
          <section class="card">
            <header class="card__header"><h3>Branch identity</h3></header>
            <div class="card__body">
              <label class="branch-create__field field">
                <span class="field__label">Branch name</span>
                <input
                  class="branch-create__name"
                  type="text"
                  formControlName="name"
                  [maxlength]="nameMaxLength"
                />
                @if (showError('name')) {
                  <span class="branch-create__error field-error" role="alert">Enter a branch name.</span>
                }
              </label>

              <label class="branch-create__field field">
                <span class="field__label">Address</span>
                <input
                  class="branch-create__address"
                  type="text"
                  formControlName="address"
                  [maxlength]="addressMaxLength"
                />
                @if (showError('address')) {
                  <span class="branch-create__error field-error" role="alert">Enter an address.</span>
                }
              </label>

              <label class="branch-create__field field">
                <span class="field__label">Contact details</span>
                <input
                  class="branch-create__contact-details"
                  type="text"
                  formControlName="contactDetails"
                  [maxlength]="contactDetailsMaxLength"
                />
                @if (showError('contactDetails')) {
                  <span class="branch-create__error field-error" role="alert">Enter contact details.</span>
                }
              </label>
            </div>
          </section>

          <section class="card">
            <header class="card__header"><h3>Cameras</h3></header>
            <div class="card__body branch-form__cameras">
              @for (cameraForm of cameraForms; track cameraForm; let i = $index) {
                <app-camera-config-form
                  [form]="cameraForm"
                  [position]="i + 1"
                  [removable]="cameras.length > 1"
                  (remove)="removeCamera(i)"
                />
              }

              <button
                class="branch-create__add-camera btn btn--ghost"
                type="button"
                (click)="addCamera()"
              >
                + Add camera
              </button>
            </div>
          </section>

          @if (failed()) {
            <!-- One fixed message for every failure mode: a Backend 400, a 500, or a dropped
                 connection. No Backend text, no status code, no echoed field values — an RTSP URL
                 may carry credentials and an error must not become the leak (FS-02 §11). A 401 never
                 reaches here; sessionExpiryInterceptor has already redirected (T-25). -->
            <p class="branch-create__error branch-create__error--submit banner banner--error" role="alert">
              The branch could not be created. Check the details and try again.
            </p>
          }

          <footer class="branch-form__actions">
            <a class="branch-form__cancel btn btn--ghost" [routerLink]="branchesRoute">Cancel</a>
            <button class="branch-create__submit btn btn--primary" type="submit" [disabled]="submitting()">
              {{ submitting() ? 'Creating…' : 'Create branch' }}
            </button>
          </footer>
        </form>
      }
    </section>
  `,
  styles: `
    .branch-form__form {
      display: flex;
      flex-direction: column;
      gap: var(--space-5);
    }

    .branch-form__cameras {
      display: flex;
      flex-direction: column;
      gap: var(--space-4);
    }

    .branch-form__actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--space-2);
      position: sticky;
      bottom: 0;
      background: var(--color-bg);
      padding: var(--space-3) 0;
    }

    .branch-create__field {
      margin-bottom: var(--space-4);
    }

    .branch-create__field:last-child {
      margin-bottom: 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchCreateComponent implements OnDestroy {
  private readonly formBuilder = inject(FormBuilder);
  private readonly branchService = inject(BranchService);
  private readonly router = inject(Router);

  protected readonly branchesRoute = BRANCHES_ROUTE;
  protected readonly nameMaxLength = BRANCH_NAME_MAX_LENGTH;
  protected readonly addressMaxLength = BRANCH_ADDRESS_MAX_LENGTH;
  protected readonly contactDetailsMaxLength = BRANCH_CONTACT_DETAILS_MAX_LENGTH;

  /**
   * The form starts with exactly one camera, because a branch requires at least one (FS-02 §12) and
   * an Admin should not have to discover that by pressing "Add camera" first.
   */
  protected readonly form = this.formBuilder.group({
    name: ['', [notBlank, Validators.maxLength(BRANCH_NAME_MAX_LENGTH)]],
    address: ['', [notBlank, Validators.maxLength(BRANCH_ADDRESS_MAX_LENGTH)]],
    contactDetails: ['', [notBlank, Validators.maxLength(BRANCH_CONTACT_DETAILS_MAX_LENGTH)]],
    cameras: this.formBuilder.array([this.buildCameraForm()]),
  });

  /** True only while a create request is in flight — the guard against a double submission. */
  protected readonly submitting = signal(false);
  protected readonly failed = signal(false);

  /**
   * The complete plaintext Activation Key, held from the create response until the Admin leaves.
   * This signal is the key's only residence anywhere in the browser.
   */
  protected readonly activationKey = signal<string | null>(null);

  /** The id of the branch just created, so "Continue to branch" knows where to go. */
  private createdBranchId: string | null = null;

  protected get cameras(): FormArray {
    return this.form.get('cameras') as FormArray;
  }

  protected get cameraForms(): FormGroup[] {
    return this.cameras.controls as FormGroup[];
  }

  ngOnDestroy(): void {
    // Belt and braces: the signal would be garbage anyway once the component goes, but the key's
    // lifetime is a stated requirement, so it is ended explicitly rather than left to the collector.
    this.clearActivationKey();
  }

  protected addCamera(): void {
    this.cameras.push(this.buildCameraForm());
  }

  /**
   * Removes a camera row, unless it is the last one — a branch must always keep at least one camera
   * (FS-02 §12). The button is not rendered for the final row, so this guard covers only a
   * programmatic call.
   */
  protected removeCamera(index: number): void {
    if (this.cameras.length <= 1) {
      return;
    }

    this.cameras.removeAt(index);
  }

  protected submit(): void {
    // Two reasons to send nothing: a request is already in flight (a double-click must not create
    // two branches, each with its own device and key), or the form is invalid — in which case the
    // Backend would reject it anyway, and the Admin is shown what to fix instead.
    if (this.submitting()) {
      return;
    }

    if (this.form.invalid) {
      // Reveals the errors on fields the Admin never touched, so an untouched blank form pressed
      // straight into submit explains itself.
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.failed.set(false);

    this.branchService.create(this.buildRequest()).subscribe({
      next: (created) => {
        this.submitting.set(false);
        // The form is left intact but unrendered; the key display replaces it. Navigation waits for
        // the Admin (FS-02 §5.1 step 7).
        this.createdBranchId = created.branchId;
        this.activationKey.set(created.activationKey);
      },
      error: () => {
        // The entered values are kept: the Admin re-submits or corrects rather than retyping the
        // branch and every camera. Nothing about the failure is logged or displayed beyond the
        // generic message.
        this.submitting.set(false);
        this.failed.set(true);
      },
    });
  }

  /**
   * Leaves the disclosure for the created branch's detail view, discarding the key first so it is
   * already gone by the time the next view exists. The destination is a plain route with only the
   * branch id in it — the key is never a route parameter, a query parameter, or router state.
   */
  protected continueToBranch(): void {
    const branchId = this.createdBranchId;
    this.clearActivationKey();

    void this.router.navigateByUrl(branchId === null ? BRANCHES_ROUTE : branchDetailRoute(branchId));
  }

  protected showError(controlName: string): boolean {
    const control = this.form.get(controlName);

    return control !== null && control.invalid && (control.touched || control.dirty);
  }

  /**
   * Builds the request body, trimming every value.
   *
   * Trimming is what makes the submitted value agree with what `notBlank` judged: a name of `'  A '`
   * is valid, and `'A'` is the name meant by it. The RTSP URL is trimmed and otherwise passed
   * through untouched — it is not parsed, split, or inspected for credentials here or anywhere.
   *
   * The shape is exactly the Backend's `CreateBranchRequestDto`: name, address, contact details, and
   * the cameras' names and RTSP URLs. No id, no device field, no activation state, no `enabled`
   * flag.
   */
  private buildRequest(): CreateBranchRequest {
    const value = this.form.getRawValue();

    return {
      name: (value.name ?? '').trim(),
      address: (value.address ?? '').trim(),
      contactDetails: (value.contactDetails ?? '').trim(),
      cameras: this.cameraForms.map((cameraForm) => {
        const camera = cameraForm.getRawValue() as { name: string; rtspUrl: string };

        return { name: camera.name.trim(), rtspUrl: camera.rtspUrl.trim() };
      }),
    };
  }

  private buildCameraForm(): FormGroup {
    return this.formBuilder.group({
      name: ['', [notBlank, Validators.maxLength(CAMERA_NAME_MAX_LENGTH)]],
      rtspUrl: ['', [notBlank, rtspUrl, Validators.maxLength(CAMERA_RTSP_URL_MAX_LENGTH)]],
    });
  }

  private clearActivationKey(): void {
    this.activationKey.set(null);
    this.createdBranchId = null;
  }
}
