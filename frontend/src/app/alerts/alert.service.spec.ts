import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { AlertDetail, AlertListResponse, defaultAlertListFilter } from './alert.models';
import { AlertService } from './alert.service';

const ALERTS_URL = `${environment.apiBaseUrl}/alerts`;
const PLACEHOLDER_ALERT_ID = '11111111-1111-1111-1111-111111111111';

function placeholderListResponse(): AlertListResponse {
  return {
    items: [
      {
        alertId: PLACEHOLDER_ALERT_ID,
        detectedAtUtc: '2026-07-29T10:00:00Z',
        receivedAtUtc: '2026-07-29T10:00:01Z',
        className: 'gun',
        confidence: 0.92,
        branchId: '22222222-2222-2222-2222-222222222222',
        branchName: 'Placeholder Branch',
        cameraId: '33333333-3333-3333-3333-333333333333',
        cameraName: 'Front Entrance',
        deviceId: '44444444-4444-4444-4444-444444444444',
        status: 'New',
        snapshotAvailable: false,
      },
    ],
    page: 1,
    pageSize: 25,
    totalCount: 1,
    totalPages: 1,
  };
}

function placeholderDetail(): AlertDetail {
  return {
    alertId: PLACEHOLDER_ALERT_ID,
    eventId: '55555555-5555-5555-5555-555555555555',
    detectedAtUtc: '2026-07-29T10:00:00Z',
    receivedAtUtc: '2026-07-29T10:00:01Z',
    deliveryLatencySeconds: 1,
    classId: 0,
    className: 'gun',
    confidence: 0.92,
    branchId: '22222222-2222-2222-2222-222222222222',
    branchName: 'Placeholder Branch',
    cameraId: '33333333-3333-3333-3333-333333333333',
    cameraName: 'Front Entrance',
    deviceId: '44444444-4444-4444-4444-444444444444',
    status: 'New',
    snapshotAvailable: false,
  };
}

describe('AlertService', () => {
  let service: AlertService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(AlertService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('sends the filter as query params and unwraps the list envelope', () => {
    let result: AlertListResponse | undefined;
    service.listAlerts(defaultAlertListFilter()).subscribe((value) => (result = value));

    const request = httpTesting.expectOne(
      (req) => req.url === ALERTS_URL && req.params.get('page') === '1',
    );
    expect(request.request.params.get('sortBy')).toBe('detectedAtUtc');
    expect(request.request.params.get('sortDescending')).toBe('true');
    request.flush({ success: true, data: placeholderListResponse() });

    expect(result).toEqual(placeholderListResponse());
  });

  it('omits optional filter params entirely when not set', () => {
    service.listAlerts(defaultAlertListFilter()).subscribe();

    const request = httpTesting.expectOne(
      (req) => req.url === ALERTS_URL && req.params.get('page') === '1',
    );
    expect(request.request.params.has('className')).toBeFalse();
    expect(request.request.params.has('snapshotAvailable')).toBeFalse();
    request.flush({ success: true, data: placeholderListResponse() });
  });

  it('includes an explicitly set optional filter', () => {
    service.listAlerts({ ...defaultAlertListFilter(), className: 'knife', snapshotAvailable: true }).subscribe();

    const request = httpTesting.expectOne(
      (req) => req.url === ALERTS_URL && req.params.get('className') === 'knife',
    );
    expect(request.request.params.get('snapshotAvailable')).toBe('true');
    request.flush({ success: true, data: placeholderListResponse() });
  });

  it('requests one Alert by id and unwraps the detail envelope', () => {
    let result: AlertDetail | null | undefined;
    service.getAlert(PLACEHOLDER_ALERT_ID).subscribe((value) => (result = value));

    const request = httpTesting.expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`);
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: placeholderDetail() });

    expect(result).toEqual(placeholderDetail());
  });

  it('resolves getAlert to null on a 404', () => {
    let result: AlertDetail | null | undefined;
    service.getAlert(PLACEHOLDER_ALERT_ID).subscribe((value) => (result = value));

    httpTesting
      .expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`)
      .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

    expect(result).toBeNull();
  });

  it('propagates a 500 from getAlert as an error', () => {
    let error: unknown;
    service.getAlert(PLACEHOLDER_ALERT_ID).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`)
      .flush({ success: false }, { status: 500, statusText: 'Internal Server Error' });

    expect(error).toBeTruthy();
  });
});
