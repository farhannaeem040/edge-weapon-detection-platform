import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, switchMap } from 'rxjs';

import { environment } from '../../environments/environment';

/**
 * The single key under which the issued JWT is held (IP-01 T-23). Every read/write of the token
 * goes through this constant so there is exactly one place the storage location is defined.
 */
export const AUTH_TOKEN_STORAGE_KEY = 'weapon-detection.auth-token';

/**
 * `sessionStorage`, not `localStorage`, is deliberate. FS-01 §7 requires the JWT to be stored "for
 * the duration of the browser session", and FS-01 §10 excludes any refresh-token flow — there is no
 * approved persistent-login behavior beyond the issued session, so the token must not outlive the
 * tab that obtained it.
 */
const tokenStorage = (): Storage => sessionStorage;

/** The uniform response envelope every /api/v1 endpoint returns (ARCH-001 §14.3 / ADR-009). */
export interface ApiEnvelope<T> {
  success: boolean;
  message?: string | null;
  data?: T | null;
  errorCode?: string | null;
}

/** `data` of a successful `POST /api/v1/auth/login` (backend `LoginResponseDto`). */
export interface LoginResponseData {
  token: string;
}

/** Request body of `POST /api/v1/auth/login` (backend `LoginRequestDto`). */
export interface LoginRequest {
  credentialIdentifier: string;
  password: string;
}

/**
 * Credential submission, token storage, and token retrieval for the Admin session (FS-01 §7).
 *
 * This service holds no authentication business logic beyond those responsibilities (NFR-MNT-001).
 * In particular it never decodes the JWT: any claim it could read client-side (expiry, subject) is
 * not authoritative. The Backend independently validates the token's signature/expiry *and* the
 * corresponding non-revoked `AdminSession` on every protected request (FS-01 §5.3, §10), and that
 * check — not anything here — is the security boundary.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  /** Absolute-or-relative per environment; the interceptor (T-24) keys off this same base URL. */
  private readonly loginUrl = `${environment.apiBaseUrl}/auth/login`;
  private readonly logoutUrl = `${environment.apiBaseUrl}/auth/logout`;

  /**
   * Submits credentials and, on success, stores the issued token. Emits nothing of value — the
   * token is intentionally not returned to callers, so no component can render or log it.
   */
  login(credentials: LoginRequest): Observable<void> {
    return this.http.post<ApiEnvelope<LoginResponseData>>(this.loginUrl, credentials).pipe(
      map((envelope) => {
        const token = envelope.data?.token;

        if (!envelope.success || !token) {
          // A 200 whose envelope carries no token is a contract violation, not a credential
          // problem; it is surfaced as an error so no half-authenticated state is created.
          throw new Error('The login response did not contain a token.');
        }

        this.storeToken(token);
      }),
    );
  }

  /**
   * Ends the session: asks the Backend to revoke the server-side `AdminSession`, then discards the
   * local token *regardless of how that request turned out* (FS-01 §5.4 step 4, §7).
   *
   * The order matters. The request is attempted first, while the token is still present for the
   * interceptor to attach — clearing first would send an anonymous logout that revokes nothing.
   * The request is never optional: discarding the token client-side does not invalidate it, so the
   * server-side revocation is the only thing that makes a copied token unusable (FS-01 §10, AC-6).
   * But a failed or unreachable Backend must still leave the browser logged out, hence the
   * unconditional clear.
   */
  logout(): Observable<void> {
    return this.http.post<ApiEnvelope<null>>(this.logoutUrl, null).pipe(
      catchError(() => of(null)),
      switchMap(() => {
        this.clearToken();
        return of(undefined);
      }),
    );
  }

  /** The stored token, or `null` when none is held. */
  getToken(): string | null {
    return tokenStorage().getItem(AUTH_TOKEN_STORAGE_KEY);
  }

  /**
   * Whether a token is held locally. This reports local state only — a token that is expired,
   * revoked, or forged still satisfies this check. It is safe for UX decisions (which view to
   * show) and never for authorization decisions (FS-01 §10).
   */
  isAuthenticated(): boolean {
    return this.getToken() !== null;
  }

  /** Discards the locally held token. Does not, by itself, invalidate it server-side. */
  clearToken(): void {
    tokenStorage().removeItem(AUTH_TOKEN_STORAGE_KEY);
  }

  private storeToken(token: string): void {
    // Never logged and never returned to a component — FS-01 §10 forbids writing JWTs to logs.
    tokenStorage().setItem(AUTH_TOKEN_STORAGE_KEY, token);
  }
}
