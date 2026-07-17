import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import { Branch } from './branch.models';

/**
 * The Dashboard's read-side client for the Branch endpoints (IP-01 T-26, consuming T-16).
 *
 * Its entire job is to call the two approved endpoints and unwrap the standard envelope
 * (ARCH-001 §14.3 / ADR-009). It performs no redaction, no derivation of activation state, and no
 * caching: the Backend's response *is* the contract, and reshaping it here would put a second,
 * divergent copy of FS-02's disclosure rules in the browser.
 *
 * The Bearer token is attached by `authInterceptor` and a 401 is handled globally by
 * `sessionExpiryInterceptor` (T-24/T-25), so neither concern appears in this service.
 */
@Injectable({ providedIn: 'root' })
export class BranchService {
  private readonly http = inject(HttpClient);
  private readonly branchesUrl = `${environment.apiBaseUrl}/branches`;

  /** Every branch, with its cameras and device summary (`GET /api/v1/branches`). */
  list(): Observable<Branch[]> {
    return this.http
      .get<ApiEnvelope<Branch[]>>(this.branchesUrl)
      .pipe(map((envelope) => unwrap(envelope)));
  }

  /**
   * One branch by id (`GET /api/v1/branches/{id}`), or `null` when the Backend answers 404.
   *
   * The null is deliberate: "no such branch" is a documented outcome of this endpoint (FS-02 §10.3),
   * not a fault, and separating it here lets the view distinguish a not-found state from a genuine
   * failure without inspecting HTTP status codes itself. Every other error propagates.
   */
  get(branchId: string): Observable<Branch | null> {
    return this.http
      .get<ApiEnvelope<Branch>>(`${this.branchesUrl}/${encodeURIComponent(branchId)}`)
      .pipe(
        map((envelope) => unwrap(envelope)),
        catchError((error: unknown) =>
          error instanceof HttpErrorResponse && error.status === 404
            ? of(null)
            : throwError(() => error),
        ),
      );
  }
}

/**
 * Extracts `data` from a success envelope.
 *
 * A 200 carrying `success: false` or no `data` is a contract violation rather than a valid empty
 * result, so it is raised as an error and reaches the view's generic failure state. The thrown
 * message is fixed and never echoes the envelope's own `message`, which is Backend text the
 * Dashboard has no reason to trust into its UI.
 */
function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (!envelope.success || envelope.data === null || envelope.data === undefined) {
    throw new Error('The Backend returned an unexpected response.');
  }

  return envelope.data;
}
