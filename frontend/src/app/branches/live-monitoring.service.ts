import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { environment } from '../../environments/environment';
import { ApiEnvelope } from '../auth/auth.service';
import {
  CreateLiveStreamRequest,
  LiveMonitoringCamera,
  LiveStreamSession,
} from './live-monitoring.models';

/**
 * The Dashboard's client for the Live Monitoring endpoints (FS-14 §5, IP-16 T-10). Mirrors
 * `AlertService`'s envelope-unwrap pattern exactly.
 */
@Injectable({ providedIn: 'root' })
export class LiveMonitoringService {
  private readonly http = inject(HttpClient);
  private readonly apiBaseUrl = environment.apiBaseUrl;

  /** The Cameras this Branch offers for live viewing (`GET .../live-monitoring/cameras`). */
  listCameras(branchId: string): Observable<LiveMonitoringCamera[]> {
    return this.http
      .get<ApiEnvelope<LiveMonitoringCamera[]>>(
        `${this.apiBaseUrl}/branches/${encodeURIComponent(branchId)}/live-monitoring/cameras`,
      )
      .pipe(map((envelope) => unwrap(envelope)));
  }

  /** Requests a browser-playable session for one Camera+mode (`POST /api/v1/live-streams`). */
  createStream(request: CreateLiveStreamRequest): Observable<LiveStreamSession> {
    return this.http
      .post<ApiEnvelope<LiveStreamSession>>(`${this.apiBaseUrl}/live-streams`, request)
      .pipe(map((envelope) => unwrap(envelope)));
  }
}

function unwrap<T>(envelope: ApiEnvelope<T>): T {
  if (!envelope.success || envelope.data === null || envelope.data === undefined) {
    throw new Error('The Backend returned an unexpected response.');
  }

  return envelope.data;
}
