import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import {
  AbstractControl,
  NonNullableFormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from './auth.service';
import { PROTECTED_LANDING_ROUTE } from './auth.routes';

/**
 * The Admin login view (FS-01 §7, IP-01 T-23).
 *
 * Collects exactly the two fields the Backend's `LoginRequestDto` defines — `credentialIdentifier`
 * and `password` — and does nothing else: no registration, password recovery, MFA, or remember-me
 * exists in FS-01.
 */
@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginComponent {
  private readonly formBuilder = inject(NonNullableFormBuilder);
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  /**
   * `Validators.required` treats a whitespace-only string as valid, so the blank case is closed
   * explicitly here. This mirrors the Backend's own `[NotBlank]` on both fields and, per FS-01
   * §11, keeps an all-blank submission from ever reaching the network.
   */
  protected readonly form = this.formBuilder.group({
    credentialIdentifier: ['', [Validators.required, notBlank]],
    password: ['', [Validators.required, notBlank]],
  });

  /** Drives the disabled state that prevents a second in-flight login request. */
  protected readonly loading = signal(false);

  /**
   * A single generic message for every failure. FS-01 §5.2/§11 requires that the view never reveal
   * whether the credential identifier or the password was wrong, so an invalid-credentials 401 and
   * an unexpected server/network fault deliberately produce the same text.
   */
  protected readonly errorMessage = signal<string | null>(null);

  protected submit(): void {
    // Guards the duplicate submission: a second click (or an Enter key) while a request is in
    // flight is ignored rather than issuing a second login.
    if (this.loading()) {
      return;
    }

    this.form.markAllAsTouched();

    if (this.form.invalid) {
      // No HTTP request is issued for an incomplete or blank form.
      return;
    }

    this.loading.set(true);
    this.errorMessage.set(null);

    // getRawValue() rather than value: the form is non-nullable, and disabled-control stripping
    // would otherwise drop fields from the payload.
    this.authService.login(this.form.getRawValue()).subscribe({
      next: () => {
        this.loading.set(false);
        void this.router.navigateByUrl(PROTECTED_LANDING_ROUTE);
      },
      error: () => {
        this.loading.set(false);
        // The password is never echoed back into the form or the message; only the password
        // control is reset so a retry does not force the identifier to be retyped.
        this.form.controls.password.reset();
        this.errorMessage.set('Login failed. Check your credentials and try again.');
      },
    });
  }
}

/** Rejects a value that is present but consists only of whitespace. */
function notBlank(control: AbstractControl<string>): ValidationErrors | null {
  return control.value.trim().length === 0 ? { blank: true } : null;
}
