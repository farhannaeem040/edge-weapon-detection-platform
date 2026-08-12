import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { DashboardService } from './dashboard.service';
import { DashboardSummary } from './dashboard.models';

const SUMMARY_URL = `${environment.apiBaseUrl}/dashboard/summary`;
const PLACEHOLDER_BRANCH_ID = '11111111-1111-1111-1111-111111111111';

function placeholderSummary(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    branch: {
      id: PLACEHOLDER_BRANCH_ID,
      name: 'Placeholder Branch',
      timeZoneId: 'Europe/London',
      localDate: '2026-07-29',
      nextQuotaResetAtUtc: '2026-07-29T23:00:00Z',
    },
    alerts: { today: 3, configuredMaximum: 15, remaining: 12, latestAlertAtUtc: '2026-07-29T10:00:00Z' },
    suppressions: { total: 0, gun: 0, knife: 0 },
    system: { deviceCount: 1, cameraCount: 1 },
    ...overrides,
  };
}

describe('DashboardService', () => {
  let service: DashboardService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(DashboardService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('requests the summary for the given Branch and unwraps the envelope', () => {
    let result: DashboardSummary | null | undefined;
    service.getSummary(PLACEHOLDER_BRANCH_ID).subscribe((value) => (result = value));

    const request = httpTesting.expectOne(
      (req) => req.url === SUMMARY_URL && req.params.get('branchId') === PLACEHOLDER_BRANCH_ID,
    );
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: placeholderSummary() });

    expect(result).toEqual(placeholderSummary());
  });

  it('sends a different branchId for a different Branch', () => {
    const otherBranchId = '22222222-2222-2222-2222-222222222222';
    service.getSummary(otherBranchId).subscribe();

    const request = httpTesting.expectOne(
      (req) => req.url === SUMMARY_URL && req.params.get('branchId') === otherBranchId,
    );
    request.flush({ success: true, data: placeholderSummary({ branch: { ...placeholderSummary().branch, id: otherBranchId } }) });
  });

  it('resolves to null when the Backend answers 404 (branchId does not resolve to any Branch)', () => {
    let result: DashboardSummary | null | undefined;
    service.getSummary(PLACEHOLDER_BRANCH_ID).subscribe((value) => (result = value));

    httpTesting
      .expectOne((req) => req.url === SUMMARY_URL)
      .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

    expect(result).toBeNull();
  });

  it('raises an error when the envelope reports success without data', () => {
    let error: unknown;
    service.getSummary(PLACEHOLDER_BRANCH_ID).subscribe({ error: (err) => (error = err) });

    httpTesting.expectOne((req) => req.url === SUMMARY_URL).flush({ success: true, data: null });

    expect(error).toBeInstanceOf(Error);
  });

  it('propagates a 500 as an error rather than null', () => {
    let error: unknown;
    service.getSummary(PLACEHOLDER_BRANCH_ID).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne((req) => req.url === SUMMARY_URL)
      .flush({ success: false }, { status: 500, statusText: 'Internal Server Error' });

    expect(error).toBeTruthy();
  });

  it('propagates a 400 as an error (missing/invalid branchId is a caller error, not "not found")', () => {
    let error: unknown;
    service.getSummary(PLACEHOLDER_BRANCH_ID).subscribe({ error: (err) => (error = err) });

    httpTesting
      .expectOne((req) => req.url === SUMMARY_URL)
      .flush({ success: false, errorCode: 'VALIDATION_ERROR' }, { status: 400, statusText: 'Bad Request' });

    expect(error).toBeTruthy();
  });
});
