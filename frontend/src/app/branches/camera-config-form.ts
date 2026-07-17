import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { FormGroup, ReactiveFormsModule } from '@angular/forms';

import { CAMERA_NAME_MAX_LENGTH, CAMERA_RTSP_URL_MAX_LENGTH } from './branch.models';

/**
 * One camera row of the branch-creation form (IP-01 T-27; FS-02 §10.1).
 *
 * It owns no state. The `FormGroup` it renders belongs to the parent's cameras `FormArray`, so
 * adding and removing rows stays a single concern in one place rather than being split across a
 * parent and its children.
 *
 * The RTSP URL is bound to a plain text input and submitted, and that is all. This component does
 * not parse the URL into parts, does not separate or store any embedded username/password, does not
 * probe the camera, and never renders the entered value back inside an error message — the value may
 * carry credentials, and the only place it is allowed to travel is the request body to the protected
 * Backend endpoint (FS-02 §11, ARCH-001 §15.6).
 */
@Component({
  selector: 'app-camera-config-form',
  imports: [ReactiveFormsModule],
  template: `
    <fieldset class="camera" [formGroup]="form()">
      <legend class="camera__legend">Camera {{ position() }}</legend>

      <label class="camera__field">
        <span>Camera name</span>
        <input
          class="camera__name"
          type="text"
          formControlName="name"
          [maxlength]="cameraNameMaxLength"
        />
      </label>
      @if (showError('name')) {
        <p class="camera__error" role="alert">Enter a camera name.</p>
      }

      <label class="camera__field">
        <span>RTSP URL</span>
        <input
          class="camera__rtsp-url"
          type="text"
          formControlName="rtspUrl"
          [maxlength]="rtspUrlMaxLength"
        />
      </label>
      @if (showError('rtspUrl')) {
        <!-- Fixed text. The submitted URL is never interpolated into an error (FS-02 §11). -->
        <p class="camera__error" role="alert">Enter a valid RTSP URL.</p>
      }

      @if (removable()) {
        <button class="camera__remove" type="button" (click)="remove.emit()">Remove camera</button>
      }
    </fieldset>
  `,
  styles: `
    .camera__field {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      padding: 0.25rem 0;
    }

    .camera__error {
      margin: 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CameraConfigFormComponent {
  /** The parent's `FormGroup` for this camera — `{ name, rtspUrl }`. */
  readonly form = input.required<FormGroup>();

  /** This camera's 1-based position, for the legend only. */
  readonly position = input.required<number>();

  /**
   * Whether this row may be removed. Decided by the parent, which is the only place that knows how
   * many cameras remain — a branch must always keep at least one (FS-02 §12), so the last row's
   * button is not rendered at all rather than rendered and quietly refused.
   */
  readonly removable = input.required<boolean>();

  readonly remove = output<void>();

  protected readonly cameraNameMaxLength = CAMERA_NAME_MAX_LENGTH;
  protected readonly rtspUrlMaxLength = CAMERA_RTSP_URL_MAX_LENGTH;

  /**
   * Errors appear once the Admin has engaged with a field or tried to submit — not while an
   * untouched, empty form is first being read.
   */
  protected showError(controlName: string): boolean {
    const control = this.form().get(controlName);

    return control !== null && control.invalid && (control.touched || control.dirty);
  }
}
