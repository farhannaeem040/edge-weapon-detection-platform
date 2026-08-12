import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { AlertDetailComponent } from './alert-detail';
import { AlertDetail } from './alert.models';

const ALERTS_URL = `${environment.apiBaseUrl}/alerts`;
const PLACEHOLDER_ALERT_ID = '11111111-1111-1111-1111-111111111111';

function placeholderDetail(overrides: Partial<AlertDetail> = {}): AlertDetail {
  return {
    alertId: PLACEHOLDER_ALERT_ID,
    eventId: '55555555-5555-5555-5555-555555555555',
    detectedAtUtc: '2026-07-29T10:00:00Z',
    receivedAtUtc: '2026-07-29T10:00:01Z',
    deliveryLatencySeconds: 1.2,
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
    ...overrides,
  };
}

describe('AlertDetailComponent', () => {
  let fixture: ComponentFixture<AlertDetailComponent>;
  let httpTesting: HttpTestingController;

  async function createWithRouteParam(alertId: string | null): Promise<void> {
    TestBed.resetTestingModule();

    await TestBed.configureTestingModule({
      imports: [AlertDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: convertToParamMap(alertId === null ? {} : { alertId }),
            },
          },
        },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(AlertDetailComponent);
  }

  beforeEach(() => createWithRouteParam(PLACEHOLDER_ALERT_ID));

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  function load(body: object, options?: { status: number; statusText: string }): void {
    fixture.detectChanges();
    httpTesting.expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`).flush(body, options);
    fixture.detectChanges();
  }

  it('loads the Alert named by the route parameter', () => {
    fixture.detectChanges();

    const request = httpTesting.expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`);
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: placeholderDetail() });
  });

  it('shows a loading state before the response arrives', () => {
    fixture.detectChanges();
    expect(text()).toContain('Loading Alert');
    httpTesting.expectOne(`${ALERTS_URL}/${PLACEHOLDER_ALERT_ID}`).flush({ success: true, data: placeholderDetail() });
  });

  it('renders the loaded Alert fields', () => {
    load({ success: true, data: placeholderDetail() });

    expect(text()).toContain('Placeholder Branch');
    expect(text()).toContain('Front Entrance');
    expect(text()).toContain('Gun');
  });

  it('renders the snapshot placeholder when no snapshot is available, with no <img> element', () => {
    load({ success: true, data: placeholderDetail({ snapshotAvailable: false }) });

    expect(text()).toContain('Snapshot evidence is not available for this Alert.');
    expect(text()).toContain('Snapshot capture is currently not enabled.');
    expect(element().querySelector('img')).toBeNull();
  });

  it('issues no snapshot HTTP request of any kind, only the one Alert-detail GET', () => {
    load({ success: true, data: placeholderDetail({ snapshotAvailable: false }) });

    // afterEach's httpTesting.verify() would already fail on any unmatched request; this asserts the
    // same thing explicitly for a snapshot-shaped URL (manual-review Finding 2).
    httpTesting.expectNone((req) => req.url.toLowerCase().includes('snapshot'));
  });

  it('can later support an opaque non-null SnapshotReference safely (snapshotAvailable: true)', () => {
    load({ success: true, data: placeholderDetail({ snapshotAvailable: true }) });

    // No placeholder, no crash, no <img>, no fabricated image — just an honest statement that
    // retrieval isn't wired into this view yet (FS-10 §1, out of scope for this feature).
    expect(text()).not.toContain('Snapshot evidence is not available for this Alert.');
    expect(text()).toContain('A snapshot was captured for this Alert.');
    expect(element().querySelector('img')).toBeNull();
    expect(element().querySelector('app-alert-snapshot-placeholder')).toBeNull();
  });

  it('shows a not-found state on a 404, distinct from a generic failure', () => {
    load({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

    expect(text()).toContain('That Alert was not found.');
  });

  it('shows a generic failure state on a 500', () => {
    load({ success: false }, { status: 500, statusText: 'Internal Server Error' });

    expect(text()).toContain('The Alert could not be loaded.');
  });

  it('treats a missing route parameter as not-found rather than requesting the Backend', async () => {
    await createWithRouteParam(null);
    fixture.detectChanges();

    httpTesting.expectNone(() => true);
    expect(text()).toContain('That Alert was not found.');
  });
});
