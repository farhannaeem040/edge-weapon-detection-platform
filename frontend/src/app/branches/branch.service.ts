import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import { Branch, CreateBranchRequest, CreatedBranch } from './branch.models';

/**
 * The Dashboard's client for the Branch endpoints (IP-01 T-26/T-27, consuming T-16).
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

  /**
   * Creates a branch with its cameras (`POST /api/v1/branches`), returning the created branch and
   * the complete plaintext Activation Key from that single response (IP-01 T-27; FS-02 §10.1).
   *
   * The key is handed straight to the caller and kept nowhere: this service does not store it on the
   * instance, cache it, or place it in any browser storage. Its single disclosure lives only as long
   * as the component holding the returned value (FS-02 §5.4, §7).
   *
   * Nothing here logs the request or the response. The request body carries an RTSP URL that may
   * embed credentials and the response carries the plaintext key, so both are exactly the payloads
   * that must never reach a console or a log (FS-02 §11, ARCH-001 §15.6). The Bearer token is
   * attached by `authInterceptor` and a 401 handled by `sessionExpiryInterceptor`, as with the reads.
   */
  create(request: CreateBranchRequest): Observable<CreatedBranch> {
    return this.http.post<ApiEnvelope<CreatedBranch>>(this.branchesUrl, request).pipe(
      map((envelope) => {
        const created = unwrap(envelope);

        // A 201 whose envelope omits the key is a contract violation, not a branch that happens to
        // have no key: the key can never be re-fetched (FS-02 §6), so surfacing this as a success
        // would silently lose it. It fails loudly into the caller's generic error state instead.
        if (!created.activationKey) {
          throw new Error('The Backend returned an unexpected response.');
        }

        return created;
      }),
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
