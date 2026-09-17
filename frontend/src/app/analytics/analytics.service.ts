import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import { AnalyticsFilterState, OperationalAnalytics } from './analytics.models';

/**
 * The Analytics page's client for the operational-analytics endpoint and its CSV export
 * (FS-15 §6; IP-17 T-12).
 *
 * One read call serves every card on the page — there is deliberately no per-chart request, so the
 * four cards cannot end up describing four slightly different moments (FS-15 §4, IP-17 §1.6). The
 * Branch dropdown is populated by the existing `BranchService.list()` the Alerts page already uses;
 * this service adds no second Branch endpoint.
 *
 * It performs no aggregation, no bucketing, and no caching of its own — those are the Backend's and
 * the calling component's concerns respectively. It only knows how to ask for one filtered snapshot
 * and how to unwrap the standard envelope (ARCH-001 §14.3 / ADR-009).
 */
@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  private readonly http = inject(HttpClient);
  private readonly operationalUrl = `${environment.apiBaseUrl}/analytics/operational`;
  private readonly exportUrl = `${environment.apiBaseUrl}/analytics/operational/export`;

  /**
   * The analytics snapshot for the given filter state, or `null` when the Backend answers 404 because
   * `branchId` does not resolve to any Branch (never existed, or deleted since) — a documented
   * outcome, not a fault, so the view can render an explicit "not found" state instead of an empty
   * dashboard that would look like a real, quiet Branch. Every other error propagates.
   */
  getOperational(filter: AnalyticsFilterState): Observable<OperationalAnalytics | null> {
    return this.http
      .get<ApiEnvelope<OperationalAnalytics>>(this.operationalUrl, { params: toParams(filter) })
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
   * The CSV export for exactly the filter state currently on screen (FS-15 §6.2).
   *
   * Fetched as a `Blob` through `HttpClient` rather than opened as a plain link, so the request
   * carries the Admin's bearer token via the existing `authInterceptor` — a bare `<a href>` would be
   * an unauthenticated navigation and simply 401. The file is generated entirely by the Backend; no
   * row data is reconstructed here.
   */
  downloadCsv(filter: AnalyticsFilterState): Observable<{ blob: Blob; fileName: string }> {
    return this.http
      .get(this.exportUrl, {
        params: toParams(filter),
        responseType: 'blob',
        observe: 'response',
      })
      .pipe(
        map((response) => ({
          blob: response.body ?? new Blob(),
          // Prefer the Backend's own filename; fall back to the same documented shape rather than to
          // a generic "download.csv" the Admin would have to rename.
          fileName:
            fileNameFromContentDisposition(response.headers.get('Content-Disposition')) ??
            `operational-analytics-${new Date().toISOString().slice(0, 10)}.csv`,
        })),
      );
  }
}

/** Builds the query string. Absent filters are omitted entirely rather than sent as empty strings,
 *  which the Backend would reject as an unrecognized value. */
function toParams(filter: AnalyticsFilterState): HttpParams {
  let params = new HttpParams().set('range', filter.range);

  if (filter.branchId) {
    // A Branch is always identified by its GUID on the wire, never by its display name (FS-15 §4.2).
    params = params.set('branchId', filter.branchId);
  }

  if (filter.detectionType) {
    params = params.set('detectionType', filter.detectionType);
  }

  return params;
}

/**
 * Extracts `data` from a success envelope. A 200 carrying `success: false` or no `data` is a contract
 * violation rather than a valid empty result, so it is raised as an error and reaches the view's
 * generic failure state — mirroring `DashboardService`'s own `unwrap`.
 */
function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (!envelope.success || envelope.data === null || envelope.data === undefined) {
    throw new Error('The Backend returned an unexpected response.');
  }

  return envelope.data;
}

/** Reads the filename from a `Content-Disposition` header, tolerating quoted and RFC 5987 forms. */
function fileNameFromContentDisposition(header: string | null): string | undefined {
  if (!header) {
    return undefined;
  }

  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    return decodeURIComponent(encoded[1]);
  }

  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : undefined;
}
