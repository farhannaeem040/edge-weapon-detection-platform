import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormArray, FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { BranchService } from './branch.service';
import { CameraConfigFormComponent } from './camera-config-form';
import {
  BRANCH_ADDRESS_MAX_LENGTH,
  BRANCH_CONTACT_DETAILS_MAX_LENGTH,
  BRANCH_NAME_MAX_LENGTH,
  CAMERA_NAME_MAX_LENGTH,
  CAMERA_RTSP_URL_MAX_LENGTH,
  UpdateBranchRequest,
} from './branch.models';
import { BRANCHES_ROUTE, BRANCH_ID_PARAM, branchDetailRoute } from './branch.routes';
import { notBlank, rtspUrl } from './branch.validators';

/**
 * Branch editing: the form that loads an existing branch, lets an Admin change its scalar fields and
 * reconcile its cameras, and saves the change through `PUT /api/v1/branches/{branchId}` (IP-03 T-45;
 * FS-03 §5.1–§5.4, §10.1, AC-1 through AC-9).
 *
 * Two things shape this view. First, unlike creation there is **no key disclosure**: an edit never
 * mints, regenerates, or reveals an Activation Key, never touches device identity or activation
 * state, and the update response is the ordinary read shape carrying none of them (FS-03 §5.4, §12).
 * So a successful save simply navigates back to the branch's detail view — there is nothing to hold
 * on screen. Second, camera **identity** is the whole point of the reconcile: each existing camera
 * keeps a hidden `cameraId` through the form, so editing it updates it in place; a camera added here
 * has none, so the Backend gives it a fresh identity; and an existing camera the Admin removes is
 * simply absent from the submission, which the Backend reads as a deletion (FS-03 §1.3, §5.2).
 *
 * The camera rows reuse `app-camera-config-form` exactly as creation does — that component renders
 * only name and RTSP URL and never sees the `cameraId`, so the hidden control rides along untouched.
 * As everywhere in this module, nothing here logs the branch, its cameras, or the request: an RTSP
 * URL may embed credentials (FS-03 §12).
 */
@Component({
  selector: 'app-branch-edit',
  imports: [ReactiveFormsModule, RouterLink, CameraConfigFormComponent],
  template: `
    <section class="branch-edit branch-form">
      <header class="branch-edit__header page-header">
        <div class="page-header__titles">
          <a class="branch-edit__back breadcrumb" [routerLink]="branchesRoute">← Branches</a>
          <h2 class="page-header__title">Edit branch</h2>
        </div>
      </header>

      @if (loading()) {
        <div class="card">
          <p class="branch-edit__status card__body status-text">
            <span class="spinner" aria-hidden="true"></span> Loading branch…
          </p>
        </div>
      } @else if (notFound()) {
        <div class="card">
          <p class="branch-edit__status card__body banner banner--info" role="alert">
            That branch was not found.
          </p>
        </div>
      } @else if (loadFailed()) {
        <!-- Generic by design; no Backend error detail is surfaced (FS-03 §12). -->
        <div class="card">
          <p class="branch-edit__status branch-edit__status--error card__body banner banner--error" role="alert">
            The branch could not be loaded. Try again.
          </p>
        </div>
      } @else {
        <form class="branch-edit__form branch-form__form" [formGroup]="form" (ngSubmit)="submit()">
          <section class="card">
            <header class="card__header"><h3>Branch identity</h3></header>
            <div class="card__body">
              <label class="branch-edit__field field">
                <span class="field__label">Branch name</span>
                <input
                  class="branch-edit__name"
                  type="text"
                  formControlName="name"
                  [maxlength]="nameMaxLength"
                />
                @if (showError('name')) {
                  <span class="branch-edit__error field-error" role="alert">Enter a branch name.</span>
                }
              </label>

              <label class="branch-edit__field field">
                <span class="field__label">Address</span>
                <input
                  class="branch-edit__address"
                  type="text"
                  formControlName="address"
                  [maxlength]="addressMaxLength"
                />
                @if (showError('address')) {
                  <span class="branch-edit__error field-error" role="alert">Enter an address.</span>
                }
              </label>

              <label class="branch-edit__field field">
                <span class="field__label">Contact details</span>
                <input
                  class="branch-edit__contact-details"
                  type="text"
                  formControlName="contactDetails"
                  [maxlength]="contactDetailsMaxLength"
                />
                @if (showError('contactDetails')) {
                  <span class="branch-edit__error field-error" role="alert">Enter contact details.</span>
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

              <button class="branch-edit__add-camera btn btn--ghost" type="button" (click)="addCamera()">
                + Add camera
              </button>
            </div>
          </section>

          @if (failed()) {
            <!-- One fixed message for every failure mode: a Backend 400 (invalid RTSP or rejected
                 camera id), a 500, or a dropped connection. No Backend text, no status code, no
                 echoed field values — an RTSP URL may carry credentials (FS-03 §12). A 401 never
                 reaches here; sessionExpiryInterceptor has already redirected (T-25). -->
            <p class="branch-edit__error branch-edit__error--submit banner banner--error" role="alert">
              The branch could not be saved. Check the details and try again.
            </p>
          }

          <footer class="branch-form__actions">
            <a class="branch-form__cancel btn btn--ghost" [routerLink]="branchesRoute">Cancel</a>
            <button class="branch-edit__submit btn btn--primary" type="submit" [disabled]="submitting()">
              {{ submitting() ? 'Saving…' : 'Save changes' }}
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

    .branch-edit__field {
      margin-bottom: var(--space-4);
    }

    .branch-edit__field:last-child {
      margin-bottom: 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchEditComponent implements OnInit {
  private readonly formBuilder = inject(FormBuilder);
  private readonly branchService = inject(BranchService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly branchesRoute = BRANCHES_ROUTE;
  protected readonly nameMaxLength = BRANCH_NAME_MAX_LENGTH;
  protected readonly addressMaxLength = BRANCH_ADDRESS_MAX_LENGTH;
  protected readonly contactDetailsMaxLength = BRANCH_CONTACT_DETAILS_MAX_LENGTH;

  /**
   * The form is built empty and populated from the loaded branch in `ngOnInit`. It is not rendered
   * until the load settles, so the empty initial cameras array is never shown.
   */
  protected readonly form = this.formBuilder.group({
    name: ['', [notBlank, Validators.maxLength(BRANCH_NAME_MAX_LENGTH)]],
    address: ['', [notBlank, Validators.maxLength(BRANCH_ADDRESS_MAX_LENGTH)]],
    contactDetails: ['', [notBlank, Validators.maxLength(BRANCH_CONTACT_DETAILS_MAX_LENGTH)]],
    cameras: this.formBuilder.array([] as FormGroup[]),
  });

  protected readonly loading = signal(true);
  protected readonly notFound = signal(false);
  protected readonly loadFailed = signal(false);

  /** True only while an update request is in flight — the guard against a double submission. */
  protected readonly submitting = signal(false);
  protected readonly failed = signal(false);

  /** The branch being edited; the id the update endpoint is addressed by. */
  private branchId: string | null = null;

  protected get cameras(): FormArray {
    return this.form.get('cameras') as FormArray;
  }

  protected get cameraForms(): FormGroup[] {
    return this.cameras.controls as FormGroup[];
  }

  ngOnInit(): void {
    const branchId = this.route.snapshot.paramMap.get(BRANCH_ID_PARAM);
    this.branchId = branchId;

    if (!branchId) {
      // Unreachable through the router (the parameter is part of the path), but a missing id is a
      // not-found, never a request to the Backend for `/branches/`.
      this.loading.set(false);
      this.notFound.set(true);
      return;
    }

    this.branchService.get(branchId).subscribe({
      next: (branch) => {
        this.loading.set(false);

        if (branch === null) {
          this.notFound.set(true);
          return;
        }

        this.form.patchValue({
          name: branch.name,
          address: branch.address,
          contactDetails: branch.contactDetails,
        });

        // One row per existing camera, each carrying its public id so the save updates it in place
        // rather than replacing it (FS-03 §5.2). A branch always has at least one camera (FS-02
        // §12), so this leaves the "final camera cannot be removed" rule already satisfied.
        for (const camera of branch.cameras) {
          this.cameras.push(this.buildCameraForm(camera.cameraId, camera.name, camera.rtspUrl));
        }
      },
      // The endpoint's documented 404 is the null above; anything else — including a 401 the
      // session-expiry interceptor has already acted on (T-25) — settles into the generic failure.
      error: () => {
        this.loading.set(false);
        this.loadFailed.set(true);
      },
    });
  }

  protected addCamera(): void {
    // A camera added here has no id: the Backend generates a fresh identity for it on save
    // (FS-03 §1.3, §5.2).
    this.cameras.push(this.buildCameraForm(null, '', ''));
  }

  /**
   * Removes a camera row, unless it is the last one — a branch must always keep at least one camera
   * (FS-03 §5.2). The button is not rendered for the final row, so this guard covers only a
   * programmatic call.
   */
  protected removeCamera(index: number): void {
    if (this.cameras.length <= 1) {
      return;
    }

    this.cameras.removeAt(index);
  }

  protected submit(): void {
    // Two reasons to send nothing: a request is already in flight (a double-click must not submit
    // the edit twice), or the form is invalid — in which case the Backend would reject it anyway,
    // and the Admin is shown what to fix instead.
    if (this.submitting() || this.branchId === null) {
      return;
    }

    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    this.submitting.set(true);
    this.failed.set(false);

    this.branchService.update(this.branchId, this.buildRequest()).subscribe({
      next: (updated) => {
        this.submitting.set(false);

        if (updated === null) {
          // The endpoint's documented 404 (FS-03 §10.1): the branch was removed between load and
          // save. Treated as a failure the Admin can see, not a silent success.
          this.failed.set(true);
          return;
        }

        // Editing changes nothing this component must hold on screen — no key, no secret — so it
        // returns to the branch's detail view (FS-03 §5.1). The destination is a plain route with
        // only the branch id in it.
        void this.router.navigateByUrl(branchDetailRoute(updated.branchId));
      },
      error: () => {
        // The entered values are kept: the Admin corrects and retries rather than re-editing the
        // branch and every camera. Nothing about the failure is logged or displayed beyond the
        // generic message.
        this.submitting.set(false);
        this.failed.set(true);
      },
    });
  }

  protected showError(controlName: string): boolean {
    const control = this.form.get(controlName);

    return control !== null && control.invalid && (control.touched || control.dirty);
  }

  /**
   * Builds the request body, trimming every value.
   *
   * The shape is exactly the Backend's `UpdateBranchRequestDto`: name, address, contact details, and
   * cameras. Each camera carries its `cameraId` only when it has one — an existing camera being
   * edited in place — and omits it entirely for a newly added camera, which is precisely how the
   * Backend tells an update from an add (FS-03 §1.3, §5.2). A removed camera simply never appears.
   */
  private buildRequest(): UpdateBranchRequest {
    const value = this.form.getRawValue();

    return {
      name: (value.name ?? '').trim(),
      address: (value.address ?? '').trim(),
      contactDetails: (value.contactDetails ?? '').trim(),
      cameras: this.cameraForms.map((cameraForm) => {
        const camera = cameraForm.getRawValue() as {
          cameraId: string | null;
          name: string;
          rtspUrl: string;
        };

        const request = { name: camera.name.trim(), rtspUrl: camera.rtspUrl.trim() };

        // Only existing cameras carry an id; a new camera's request must have no `cameraId` member
        // at all, not a null one.
        return camera.cameraId ? { cameraId: camera.cameraId, ...request } : request;
      }),
    };
  }

  /**
   * Builds one camera row. `cameraId` is a plain, unvalidated control that the Admin never sees and
   * `app-camera-config-form` never reads: it exists only to carry an existing camera's identity
   * through the form to the request. Name and RTSP URL carry the same validators as creation.
   */
  private buildCameraForm(cameraId: string | null, name: string, url: string): FormGroup {
    return this.formBuilder.group({
      cameraId: [cameraId],
      name: [name, [notBlank, Validators.maxLength(CAMERA_NAME_MAX_LENGTH)]],
      rtspUrl: [url, [notBlank, rtspUrl, Validators.maxLength(CAMERA_RTSP_URL_MAX_LENGTH)]],
    });
  }
}
