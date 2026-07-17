import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import {
  Branch,
  CreateBranchRequest,
  CreatedBranch,
  RegeneratedActivationKey,
  UpdateBranchRequest,
} from './branch.models';

/**
 * The Dashboard's client for the Branch endpoints (IP-01 T-26/T-27/T-28, consuming T-16/T-18).
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

  /**
   * The device endpoints' base (IP-01 §10). Regeneration lives under `/devices` even though it is
   * addressed by a *branch* id — see `regenerateActivationKey`.
   */
  private readonly devicesUrl = `${environment.apiBaseUrl}/devices`;

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

  /**
   * Updates a branch and reconciles its cameras (`PUT /api/v1/branches/{branchId}`), returning the
   * safe updated branch, or `null` when the Backend answers 404 (IP-03 T-43; FS-03 §10.1).
   *
   * The response is the Backend's ordinary read shape (`BranchResponseDto.ForRead`): it carries no
   * Activation Key, no key status, no `DeviceRecordId`, and no secret — editing never mints or
   * exposes any of them (FS-03 §7.1, §12), and `Branch` cannot model them regardless. Camera
   * identity is the request's own contract: each camera's optional `cameraId` distinguishes an edit
   * in place from an add, and an omitted existing camera is a removal (see `UpdateBranchRequest`).
   *
   * The `null` follows `get`/`regenerateActivationKey`: a not-found branch is a documented outcome of
   * this endpoint (FS-03 §10.1), so the view can tell it apart from a genuine failure without reading
   * status codes. Every other error — a 400 for an invalid RTSP URL or a rejected camera id, a 401
   * the session-expiry interceptor has already acted on, a 500 — propagates. Nothing here logs the
   * request or response: the body carries RTSP URLs that may embed credentials (FS-03 §12).
   */
  update(branchId: string, request: UpdateBranchRequest): Observable<Branch | null> {
    return this.http
      .put<ApiEnvelope<Branch>>(
        `${this.branchesUrl}/${encodeURIComponent(branchId)}`,
        request,
      )
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
   * Regenerates a branch's Activation Key (`POST /api/v1/devices/{branchId}/activation-key/
   * regenerate`), returning the new complete plaintext key from that single response, or `null` when
   * the Backend answers 404 (IP-01 T-28; FS-02 §5.3, §10.2, AC-5).
   *
   * **The identifier is the branch id, not a Device ID.** That is the Backend's contract, not a
   * convenience here: regeneration must work for a Device that has never activated (FS-02 §15 T-03),
   * whose `DeviceId` is still null and cannot address it, while the internal `DeviceRecordId` is
   * never exposed to any client (FS-02 §1.3). The branch id is the only always-present, client-visible
   * handle to a branch's single Device, so the route's `{id}` is populated with it — as
   * `DeviceController.RegenerateActivationKey`'s own `branchId` parameter spells out.
   *
   * There is no request body: the branch id in the route is the endpoint's only input.
   *
   * The `null` follows `get`'s reasoning — a regeneration target that does not exist is a documented
   * outcome (FS-02 §13), so the view can distinguish it from a genuine failure without reading status
   * codes. Every other error, a 401 included, propagates.
   *
   * As with `create`, the key is handed straight to the caller and kept nowhere: no field on this
   * service, no cache, no browser storage, and nothing here logs the response. The old key is neither
   * sent nor requested — it cannot be: the Backend has only its hash, and regeneration has already
   * invalidated it (FS-02 §5.3 step 3).
   */
  regenerateActivationKey(branchId: string): Observable<string | null> {
    return this.http
      .post<ApiEnvelope<RegeneratedActivationKey>>(
        `${this.devicesUrl}/${encodeURIComponent(branchId)}/activation-key/regenerate`,
        null,
      )
      .pipe(
        map((envelope) => {
          const { activationKey } = unwrap(envelope);

          // As at creation: a success whose envelope omits the key is a contract violation, not a
          // regeneration that produced no key. The previous key is already invalid and the new one
          // can never be re-fetched (FS-02 §6), so this must fail loudly rather than report a success
          // that silently lost it.
          if (!activationKey) {
            throw new Error('The Backend returned an unexpected response.');
          }

          return activationKey;
        }),
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
