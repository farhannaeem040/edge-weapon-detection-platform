import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import { AlertDetail, AlertListFilter, AlertListResponse } from './alert.models';

/**
 * The Dashboard's client for the Alert list/detail endpoints (FS-10 §9.2/§9.3; IP-12 T-199).
 *
 * Calls the approved endpoints and unwraps the standard envelope (ARCH-001 §14.3 / ADR-009); it
 * performs no filtering, sorting, or pagination of its own — every one of those parameters is sent to
 * the Backend as a query-string value and the Backend's response is rendered as-is. This is what keeps
 * the "server-side pagination, never a client-side slice of a full table" requirement true.
 */
@Injectable({ providedIn: 'root' })
export class AlertService {
  private readonly http = inject(HttpClient);
  private readonly alertsUrl = `${environment.apiBaseUrl}/alerts`;

  /** A page of Alerts matching the given filter (`GET /api/v1/alerts`). */
  listAlerts(filter: AlertListFilter): Observable<AlertListResponse> {
    return this.http
      .get<ApiEnvelope<AlertListResponse>>(this.alertsUrl, { params: toHttpParams(filter) })
      .pipe(map((envelope) => unwrap(envelope)));
  }

  /**
   * One Alert by id (`GET /api/v1/alerts/{id}`), or `null` when the Backend answers 404 — a
   * documented outcome (FS-10 §9.3), not a fault. Every other error propagates.
   */
  getAlert(alertId: string): Observable<AlertDetail | null> {
    return this.http
      .get<ApiEnvelope<AlertDetail>>(`${this.alertsUrl}/${encodeURIComponent(alertId)}`)
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
 * Builds the query string exactly as `AlertListRequestDto` accepts it. Only fields the filter actually
 * carries are sent — an absent optional filter means "no filter", not an empty-string one.
 */
function toHttpParams(filter: AlertListFilter): HttpParams {
  let params = new HttpParams()
    .set('page', filter.page)
    .set('pageSize', filter.pageSize)
    .set('sortBy', filter.sortBy)
    .set('sortDescending', filter.sortDescending);

  if (filter.fromUtc) {
    params = params.set('fromUtc', filter.fromUtc);
  }
  if (filter.toUtc) {
    params = params.set('toUtc', filter.toUtc);
  }
  if (filter.className) {
    params = params.set('className', filter.className);
  }
  if (filter.branchId) {
    params = params.set('branchId', filter.branchId);
  }
  if (filter.cameraId) {
    params = params.set('cameraId', filter.cameraId);
  }
  if (filter.status) {
    params = params.set('status', filter.status);
  }
  if (filter.snapshotAvailable !== undefined) {
    params = params.set('snapshotAvailable', filter.snapshotAvailable);
  }

  return params;
}

/**
 * Extracts `data` from a success envelope. A 200 carrying `success: false` or no `data` is a contract
 * violation rather than a valid empty result, so it is raised as an error and reaches the view's
 * generic failure state.
 */
function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (!envelope.success || envelope.data === null || envelope.data === undefined) {
    throw new Error('The Backend returned an unexpected response.');
  }

  return envelope.data;
}
