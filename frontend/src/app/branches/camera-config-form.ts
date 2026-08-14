import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { FormGroup, ReactiveFormsModule } from '@angular/forms';

import {
  CAMERA_KEY_MAX_LENGTH,
  CAMERA_NAME_MAX_LENGTH,
  CAMERA_RTSP_URL_MAX_LENGTH,
} from './branch.models';

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
      <div class="camera__head">
        <legend class="camera__legend">Camera {{ position() }}</legend>
        @if (removable()) {
          <button class="camera__remove btn btn--ghost" type="button" (click)="remove.emit()">
            Remove
          </button>
        }
      </div>

      <label class="camera__field field">
        <span class="field__label">Camera name</span>
        <input
          class="camera__name"
          type="text"
          formControlName="name"
          [maxlength]="cameraNameMaxLength"
        />
        @if (showError('name')) {
          <span class="camera__error field-error" role="alert">Enter a camera name.</span>
        }
      </label>

      @if (keyReadOnly()) {
        <!--
          FS-12 §2: the key is immutable after creation because it forms the public stream URL, so an
          edit form shows it as read-only text rather than a disabled input. A disabled input still
          looks like a control that ought to become editable; static text does not make the promise.
        -->
        <div class="camera__field field">
          <span class="field__label" id="camera-key-label-{{ position() }}">Camera key</span>
          <p
            class="camera__key-readonly"
            [attr.aria-labelledby]="'camera-key-label-' + position()"
          >
            {{ form().get('cameraKey')?.value }}
          </p>
          <span class="camera__hint field-hint">
            Camera key is permanent because it forms the public stream URL.
          </span>
        </div>
      } @else {
        <label class="camera__field field">
          <span class="field__label">Camera key</span>
          <input
            class="camera__key"
            type="text"
            formControlName="cameraKey"
            [maxlength]="cameraKeyMaxLength"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            [attr.aria-describedby]="'camera-key-hint-' + position()"
          />
          <span class="camera__hint field-hint" id="camera-key-hint-{{ position() }}">
            Use lowercase letters, numbers and hyphens. Example: front-entrance.
          </span>
          @if (suggestable()) {
            <button
              class="camera__key-suggest btn btn--ghost"
              type="button"
              (click)="suggestKey()"
            >
              Suggest key from camera name
            </button>
          }
          @if (showError('cameraKey')) {
            <span class="camera__error field-error" role="alert">{{ cameraKeyError() }}</span>
          }
        </label>
      }

      <label class="camera__field field">
        <span class="field__label">RTSP URL</span>
        <input
          class="camera__rtsp-url"
          type="text"
          formControlName="rtspUrl"
          [maxlength]="rtspUrlMaxLength"
        />
        @if (showError('rtspUrl')) {
          <!-- Fixed text. The submitted URL is never interpolated into an error (FS-02 §11). -->
          <span class="camera__error field-error" role="alert">Enter a valid RTSP URL.</span>
        }
      </label>
    </fieldset>
  `,
  styles: `
    .camera {
      margin: 0;
      padding: var(--space-4);
      border: 1px solid var(--color-border);
      border-radius: var(--radius);
      background: var(--color-surface-subtle);
    }

    .camera__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      margin-bottom: var(--space-3);
    }

    .camera__legend {
      float: none;
      padding: 0;
      font-family: var(--font-heading);
      font-weight: var(--weight-semibold);
      font-size: var(--text-sm);
      color: var(--color-text);
    }

    .camera__remove {
      min-height: 2rem;
      padding: 0.3rem 0.7rem;
      color: var(--color-danger);
    }

    .camera__field {
      margin-bottom: var(--space-3);
    }

    .camera__field:last-child {
      margin-bottom: 0;
    }

    .camera__key-readonly {
      margin: 0;
      font-family: var(--font-mono, monospace);
      color: var(--color-text);
    }

    .camera__key-suggest {
      align-self: flex-start;
      min-height: 2rem;
      margin-top: var(--space-2);
      padding: 0.3rem 0.7rem;
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

  /**
   * Whether this row's key is fixed (FS-12 §2). True on the edit form, where the key already exists
   * and forms a live RTSP mount; false on the create form, where the Admin is choosing it.
   */
  readonly keyReadOnly = input(false);

  /** Whether to offer the optional "suggest key from camera name" action. */
  readonly suggestable = input(true);

  readonly remove = output<void>();

  protected readonly cameraNameMaxLength = CAMERA_NAME_MAX_LENGTH;
  protected readonly cameraKeyMaxLength = CAMERA_KEY_MAX_LENGTH;
  protected readonly rtspUrlMaxLength = CAMERA_RTSP_URL_MAX_LENGTH;

  /**
   * Errors appear once the Admin has engaged with a field or tried to submit — not while an
   * untouched, empty form is first being read.
   */
  protected showError(controlName: string): boolean {
    const control = this.form().get(controlName);

    return control !== null && control.invalid && (control.touched || control.dirty);
  }

  /**
   * Which key rule was broken. Distinct messages because each implies a different fix: a reserved key
   * needs a different word, a malformed one needs different characters, and a duplicate needs to
   * differ from a sibling row.
   */
  protected cameraKeyError(): string {
    const errors = this.form().get('cameraKey')?.errors ?? {};

    if (errors['required']) {
      return 'Enter a camera key.';
    }
    if (errors['cameraKeyReserved']) {
      return 'That camera key is reserved. Choose a different one.';
    }
    if (errors['cameraKeyDuplicate']) {
      return 'Each camera in this branch needs a different key.';
    }
    if (errors['cameraKeyLength']) {
      return 'Use between 3 and 64 characters.';
    }
    if (errors['cameraKeyBackend']) {
      return typeof errors['cameraKeyBackend'] === 'string'
        ? (errors['cameraKeyBackend'] as string)
        : 'The Backend rejected this camera key.';
    }

    return 'Use lowercase letters, numbers and hyphens, starting and ending with a letter or number.';
  }

  /**
   * Fills the key from the camera name, on explicit request only (FS-12 §3 / task Phase 4).
   *
   * Three deliberate restraints: it never runs on its own, it never overwrites a non-empty key, and
   * its output is written into the input for the Admin to review and edit rather than submitted. The
   * suggestion is also not assumed unique — the array-level uniqueness validator and the Backend
   * both still judge it.
   */
  protected suggestKey(): void {
    const control = this.form().get('cameraKey');
    if (control === null) {
      return;
    }

    const current: unknown = control.value;
    if (typeof current === 'string' && current.trim().length > 0) {
      return;
    }

    const nameValue: unknown = this.form().get('name')?.value;
    if (typeof nameValue !== 'string') {
      return;
    }

    const suggestion = nameValue
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');

    if (suggestion.length === 0) {
      return;
    }

    control.setValue(suggestion);
    control.markAsDirty();
  }
}
