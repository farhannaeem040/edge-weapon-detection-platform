import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import { DashboardSummary } from './dashboard.models';

/**
 * The Dashboard's client for the operational-summary endpoint (FS-10 §9.1; IP-12 T-199; manual-review
 * Correction 3: the Backend now requires an explicit `branchId` — there is no "default Branch").
 *
 * A single read call, unwrapping the standard envelope (ARCH-001 §14.3 / ADR-009). It performs no
 * derivation of quota state, no polling, and no caching of its own — those are the calling
 * component's concerns (FS-10 §11); this service only knows how to fetch one Branch's summary.
 */
@Injectable({ providedIn: 'root' })
export class DashboardService {
  private readonly http = inject(HttpClient);
  private readonly summaryUrl = `${environment.apiBaseUrl}/dashboard/summary`;

  /**
   * The given Branch's operational summary, or `null` when the Backend answers 404 because
   * `branchId` does not resolve to any Branch (never existed, or deleted since) — a documented
   * outcome, not a fault, so the view can render an explicit "not found" state instead of a generic
   * failure. Every other error propagates.
   */
  getSummary(branchId: string): Observable<DashboardSummary | null> {
    const params = new HttpParams().set('branchId', branchId);

    return this.http.get<ApiEnvelope<DashboardSummary>>(this.summaryUrl, { params }).pipe(
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
