import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { AnalyticsService } from './analytics.service';
import { OperationalAnalytics } from './analytics.models';

const OPERATIONAL_URL = `${environment.apiBaseUrl}/analytics/operational`;
const EXPORT_URL = `${environment.apiBaseUrl}/analytics/operational/export`;
const BRANCH_ID = '9b6796f7-b2f2-47f3-a2df-a06bf94c1345';

function placeholderAnalytics(): OperationalAnalytics {
  return {
    filters: {
      range: 'last30d',
      fromUtc: '2026-07-18T00:00:00Z',
      toUtc: '2026-08-17T00:00:00Z',
      bucket: 'day',
      branchId: null,
      branchName: null,
      detectionType: null,
    },
    summary: {
      totalDetections: 4,
      suppressedDetections: 2,
      meanConfidence: 0.8,
      confidenceHigh: 3,
      confidenceMedium: 1,
      confidenceLow: 0,
      highConfidenceThreshold: 0.75,
      mediumConfidenceThreshold: 0.5,
      averageDeliveryLatencyMs: 2400,
      medianDeliveryLatencyMs: 2200,
      maxDeliveryLatencyMs: 9000,
      latencySampleCount: 4,
      latencySamplesExcluded: 0,
      alertsWithSnapshot: 3,
      branchCount: 1,
      cameraCount: 2,
      deviceCount: 1,
      validationDataAvailable: false,
      generatedAtUtc: '2026-08-17T12:00:00Z',
    },
    detectionsOverTime: [{ periodStartUtc: '2026-08-17T00:00:00Z', count: 4 }],
    detectionsByBranch: [{ branchId: BRANCH_ID, branchName: 'Ljmu Branch', count: 4 }],
    latencyOverTime: [{ periodStartUtc: '2026-08-17T00:00:00Z', averageMs: 2400, sampleCount: 4 }],
  };
}

describe('AnalyticsService', () => {
  let service: AnalyticsService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(AnalyticsService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('requests the default range and unwraps the envelope', () => {
    let result: OperationalAnalytics | null | undefined;
    service.getOperational({ range: 'last30d' }).subscribe((value) => (result = value));

    const request = httpTesting.expectOne(
      (req) => req.url === OPERATIONAL_URL && req.params.get('range') === 'last30d',
    );
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: placeholderAnalytics() });

    expect(result).toEqual(placeholderAnalytics());
  });

  it('omits the Branch and detection-type params when the filter is "all"', () => {
    service.getOperational({ range: 'last7d' }).subscribe();

    const request = httpTesting.expectOne((req) => req.url === OPERATIONAL_URL);
    // Absent, never an empty string — the Backend rejects an unrecognized value rather than
    // treating it as "all".
    expect(request.request.params.has('branchId')).toBeFalse();
    expect(request.request.params.has('detectionType')).toBeFalse();
    request.flush({ success: true, data: placeholderAnalytics() });
  });

  it('sends the Branch by its GUID, never by its display name', () => {
    service.getOperational({ range: 'last30d', branchId: BRANCH_ID }).subscribe();

    const request = httpTesting.expectOne(
      (req) => req.url === OPERATIONAL_URL && req.params.get('branchId') === BRANCH_ID,
    );
    request.flush({ success: true, data: placeholderAnalytics() });
  });

  it('sends the selected detection type', () => {
    service.getOperational({ range: 'last24h', detectionType: 'knife' }).subscribe();

    const request = httpTesting.expectOne(
      (req) =>
        req.url === OPERATIONAL_URL &&
        req.params.get('range') === 'last24h' &&
        req.params.get('detectionType') === 'knife',
    );
    request.flush({ success: true, data: placeholderAnalytics() });
  });

  it('resolves to null when the Backend answers 404 (the Branch no longer exists)', () => {
    let result: OperationalAnalytics | null | undefined;
    service
      .getOperational({ range: 'last30d', branchId: BRANCH_ID })
      .subscribe((value) => (result = value));

    httpTesting
      .expectOne((req) => req.url === OPERATIONAL_URL)
      .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

    expect(result).toBeNull();
  });

  it('raises an error when the envelope reports success without data', () => {
    let error: unknown;
    service.getOperational({ range: 'last30d' }).subscribe({ error: (err) => (error = err) });

    httpTesting.expectOne((req) => req.url === OPERATIONAL_URL).flush({ success: true, data: null });

    expect(error).toBeInstanceOf(Error);
  });

  it('propagates a 500 as an error rather than null', () => {
    let error: unknown;
    service.getOperational({ range: 'last30d' }).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne((req) => req.url === OPERATIONAL_URL)
      .flush({ success: false }, { status: 500, statusText: 'Internal Server Error' });

    expect(error).toBeTruthy();
  });

  it('propagates a 400 as an error (an invalid filter is a caller error, not "no data")', () => {
    let error: unknown;
    service.getOperational({ range: 'last30d' }).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne((req) => req.url === OPERATIONAL_URL)
      .flush(
        { success: false, errorCode: 'VALIDATION_ERROR' },
        { status: 400, statusText: 'Bad Request' },
      );

    expect(error).toBeTruthy();
  });

  it('requests the CSV export with exactly the same filters as the view', () => {
    service
      .downloadCsv({ range: 'last7d', branchId: BRANCH_ID, detectionType: 'gun' })
      .subscribe();

    const request = httpTesting.expectOne(
      (req) =>
        req.url === EXPORT_URL &&
        req.params.get('range') === 'last7d' &&
        req.params.get('branchId') === BRANCH_ID &&
        req.params.get('detectionType') === 'gun',
    );
    // A Blob through HttpClient, so the bearer token is attached by the existing auth interceptor —
    // a bare link navigation would be unauthenticated and 401.
    expect(request.request.responseType).toBe('blob');
    request.flush(new Blob(['a,b\r\n'], { type: 'text/csv' }));
  });

  it('reads the filename from the Content-Disposition header', () => {
    let fileName: string | undefined;
    service.downloadCsv({ range: 'last30d' }).subscribe((result) => (fileName = result.fileName));

    httpTesting
      .expectOne((req) => req.url === EXPORT_URL)
      .flush(new Blob(['a,b\r\n'], { type: 'text/csv' }), {
        headers: {
          'Content-Disposition': 'attachment; filename="operational-analytics-2026-08-17.csv"',
        },
      });

    expect(fileName).toBe('operational-analytics-2026-08-17.csv');
  });

  it('falls back to a sensible filename when the header is absent', () => {
    let fileName: string | undefined;
    service.downloadCsv({ range: 'last30d' }).subscribe((result) => (fileName = result.fileName));

    httpTesting
      .expectOne((req) => req.url === EXPORT_URL)
      .flush(new Blob(['a,b\r\n'], { type: 'text/csv' }));

    expect(fileName).toMatch(/^operational-analytics-\d{4}-\d{2}-\d{2}\.csv$/);
  });

  it('propagates an export failure so the page can explain it', () => {
    let error: unknown;
    service.downloadCsv({ range: 'last90d' }).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne((req) => req.url === EXPORT_URL)
      .flush(new Blob(), { status: 400, statusText: 'Bad Request' });

    expect(error).toBeTruthy();
  });
});
