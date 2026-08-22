import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { OperationalAnalytics } from './analytics.models';
import { OperationalAnalyticsComponent } from './operational-analytics';

const OPERATIONAL_URL = `${environment.apiBaseUrl}/analytics/operational`;
const EXPORT_URL = `${environment.apiBaseUrl}/analytics/operational/export`;
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;

const BRANCH_ID = '9b6796f7-b2f2-47f3-a2df-a06bf94c1345';
const OTHER_BRANCH_ID = '11111111-2222-3333-4444-555555555555';

function analytics(overrides: Partial<OperationalAnalytics> = {}): OperationalAnalytics {
  return {
    filters: {
      range: 'last30d',
      fromUtc: '2026-07-18T00:00:00Z',
      toUtc: '2026-08-17T12:00:00Z',
      bucket: 'day',
      branchId: null,
      branchName: null,
      detectionType: null,
    },
    summary: {
      totalDetections: 594,
      suppressedDetections: 2154,
      meanConfidence: 0.832,
      confidenceHigh: 500,
      confidenceMedium: 80,
      confidenceLow: 14,
      highConfidenceThreshold: 0.75,
      mediumConfidenceThreshold: 0.5,
      averageDeliveryLatencyMs: 2489.8,
      medianDeliveryLatencyMs: 2440,
      maxDeliveryLatencyMs: 9389,
      latencySampleCount: 593,
      latencySamplesExcluded: 1,
      alertsWithSnapshot: 568,
      branchCount: 1,
      cameraCount: 2,
      deviceCount: 1,
      validationDataAvailable: false,
      generatedAtUtc: '2026-08-17T12:00:00Z',
    },
    detectionsOverTime: [
      { periodStartUtc: '2026-08-15T00:00:00Z', count: 0 },
      { periodStartUtc: '2026-08-16T00:00:00Z', count: 415 },
      { periodStartUtc: '2026-08-17T00:00:00Z', count: 179 },
    ],
    detectionsByBranch: [{ branchId: BRANCH_ID, branchName: 'Ljmu Branch', count: 594 }],
    latencyOverTime: [
      { periodStartUtc: '2026-08-15T00:00:00Z', averageMs: null, sampleCount: 0 },
      { periodStartUtc: '2026-08-16T00:00:00Z', averageMs: 2500, sampleCount: 415 },
      { periodStartUtc: '2026-08-17T00:00:00Z', averageMs: 2400, sampleCount: 178 },
    ],
    ...overrides,
  };
}

const BRANCHES = [
  { branchId: BRANCH_ID, name: 'Ljmu Branch', address: '1 High Street', contactDetails: 'ops@ljmu' },
  { branchId: OTHER_BRANCH_ID, name: 'Second Branch', address: '2 High Street', contactDetails: 'ops2@ljmu' },
];

describe('OperationalAnalyticsComponent', () => {
  let fixture: ComponentFixture<OperationalAnalyticsComponent>;
  let httpTesting: HttpTestingController;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [OperationalAnalyticsComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
  });

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  /** Creates the component and answers the Branch-list request; leaves the analytics request open. */
  function create(): TestRequest {
    fixture = TestBed.createComponent(OperationalAnalyticsComponent);
    fixture.detectChanges();

    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: BRANCHES });

    return httpTesting.expectOne((req) => req.url === OPERATIONAL_URL);
  }

  function load(overrides: Partial<OperationalAnalytics> = {}): void {
    create().flush({ success: true, data: analytics(overrides) });
    fixture.detectChanges();
  }

  function select(id: string): HTMLSelectElement {
    return element().querySelector(`#${id}`) as HTMLSelectElement;
  }

  function choose(id: string, value: string): void {
    const control = select(id);
    control.value = value;
    control.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  }

  // ---------------------------------------------------------------- Header and layout

  it('renders the page heading and subtitle', () => {
    load();

    expect(element().querySelector('.page-header__title')!.textContent).toContain(
      'Operational Analytics',
    );
    expect(element().textContent).toContain('Performance metrics');
  });

  it('renders the Stitch-derived sections: filter bar, four cards, and a status footer', () => {
    load();

    expect(element().querySelector('.analytics__filters')).not.toBeNull();
    expect(element().querySelectorAll('.analytics__card').length).toBe(4);
    expect(element().querySelector('.analytics__status')).not.toBeNull();
  });

  it('lays the cards out on the Stitch 8/4 + 6/6 bento grid', () => {
    load();

    expect(element().querySelectorAll('.analytics__card--wide').length).toBe(1);
    expect(element().querySelectorAll('.analytics__card--narrow').length).toBe(1);
    expect(element().querySelectorAll('.analytics__card--half').length).toBe(2);
  });

  it('preserves LJMU branding and adopts none of the Stitch persona or product name', () => {
    load();

    const text = element().textContent ?? '';
    for (const forbidden of ['Sentinel AI', 'Alex Rivers', 'Chief Security Officer', 'Enterprise Security']) {
      expect(text).not.toContain(forbidden);
    }
  });

  it('shows no demonstration figure carried over from the mockup', () => {
    load();

    const text = element().textContent ?? '';
    for (const fabricated of ['98.4%', '12,402', '1,240 Edge Nodes', 'Logistics Hub', 'North Data']) {
      expect(text).not.toContain(fabricated);
    }
  });

  // ---------------------------------------------------------------- Filters

  it('requests the default filters on first load', () => {
    const request = create();

    expect(request.request.params.get('range')).toBe('last30d');
    expect(request.request.params.has('branchId')).toBeFalse();
    expect(request.request.params.has('detectionType')).toBeFalse();

    request.flush({ success: true, data: analytics() });
  });

  it('defaults the filter controls to Last 30 days / All Branches / All detections', () => {
    load();

    expect(select('analytics-range').value).toBe('last30d');
    expect(select('analytics-branch').value).toBe('');
    expect(select('analytics-detection-type').value).toBe('');
  });

  it('populates the Branch dropdown from the real Branch API', () => {
    load();

    const options = Array.from(select('analytics-branch').options).map((option) => option.textContent?.trim());
    expect(options).toEqual(['All Branches', 'Ljmu Branch', 'Second Branch']);
  });

  it('offers only the detection types this platform actually detects', () => {
    load();

    const options = Array.from(select('analytics-detection-type').options).map((option) =>
      option.textContent?.trim(),
    );
    expect(options).toEqual(['All detections', 'Gun', 'Knife']);
  });

  it('offers the documented date-range presets', () => {
    load();

    const options = Array.from(select('analytics-range').options).map((option) => option.value);
    expect(options).toEqual(['last24h', 'last7d', 'last30d', 'last90d']);
  });

  it('navigates on a date-range change so the URL stays the source of truth', () => {
    load();
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);

    choose('analytics-range', 'last7d');

    expect(navigate).toHaveBeenCalled();
    expect(navigate.calls.mostRecent().args[1]!.queryParams!['range']).toBe('last7d');
  });

  it('navigates on a Branch change, carrying the Branch GUID rather than its name', () => {
    load();
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);

    choose('analytics-branch', BRANCH_ID);

    expect(navigate.calls.mostRecent().args[1]!.queryParams!['branchId']).toBe(BRANCH_ID);
  });

  it('navigates on a detection-type change', () => {
    load();
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);

    choose('analytics-detection-type', 'knife');

    expect(navigate.calls.mostRecent().args[1]!.queryParams!['detectionType']).toBe('knife');
  });

  it('clears the Branch param when All Branches is selected again', () => {
    load();
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);

    choose('analytics-branch', '');

    expect(navigate.calls.mostRecent().args[1]!.queryParams!['branchId']).toBeNull();
  });

  it('issues exactly one analytics request while the Branch list and filters initialise', () => {
    // The URL is the only fetch trigger, so an asynchronously arriving Branch list must not provoke a
    // second identical request.
    create().flush({ success: true, data: analytics() });
    fixture.detectChanges();

    httpTesting.verify();
  });

  it('refetches the current filters when Refresh is pressed', () => {
    load();

    (element().querySelector('.analytics__filters-actions .icon-btn') as HTMLButtonElement).click();
    fixture.detectChanges();

    const request = httpTesting.expectOne((req) => req.url === OPERATIONAL_URL);
    expect(request.request.params.get('range')).toBe('last30d');
    request.flush({ success: true, data: analytics() });
  });

  // ---------------------------------------------------------------- States

  it('shows a loading state on every card before the response arrives', () => {
    const pending = create();
    fixture.detectChanges();

    expect(element().querySelectorAll('.analytics__pending').length).toBe(4);
    expect(element().textContent).toContain('Loading analytics…');

    pending.flush({ success: true, data: analytics() });
  });

  it('shows an error state on every card when the request fails', () => {
    create().flush({ success: false }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(element().querySelectorAll('.analytics__pending').length).toBe(4);
    expect(element().textContent).toContain('Analytics could not be loaded.');
    // The status footer is omitted rather than shown with placeholders (FS-15 §5.6).
    expect(element().querySelector('.analytics__status')).toBeNull();
  });

  it('distinguishes a deleted Branch from a quiet one', () => {
    create().flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();

    expect(element().textContent).toContain('That Branch no longer exists.');
  });

  it('shows a chart empty state, not zeros, when the filters match nothing', () => {
    load({
      summary: {
        ...analytics().summary,
        totalDetections: 0,
        meanConfidence: null,
        confidenceHigh: 0,
        confidenceMedium: 0,
        confidenceLow: 0,
        averageDeliveryLatencyMs: null,
        medianDeliveryLatencyMs: null,
        maxDeliveryLatencyMs: null,
        latencySampleCount: 0,
        latencySamplesExcluded: 0,
      },
      detectionsOverTime: [{ periodStartUtc: '2026-08-17T00:00:00Z', count: 0 }],
      latencyOverTime: [{ periodStartUtc: '2026-08-17T00:00:00Z', averageMs: null, sampleCount: 0 }],
    });

    expect(element().textContent).toContain('No delivery-latency samples in the selected range.');
    expect(element().textContent).toContain('—');
    expect(element().textContent).not.toContain('0.0%');
  });

  // ---------------------------------------------------------------- Charts and metrics

  it('renders all four charts from the response', () => {
    load();

    expect(element().querySelectorAll('app-analytics-area-chart').length).toBe(2);
    expect(element().querySelectorAll('app-analytics-bar-chart').length).toBe(1);
    expect(element().querySelectorAll('app-analytics-donut-chart').length).toBe(1);
  });

  it('plots one detections point per returned bucket, including the zero buckets', () => {
    load();

    const markers = element().querySelectorAll('app-analytics-area-chart .chart__marker');
    // The detections chart measures all three buckets (one of them a real zero).
    expect(markers.length).toBeGreaterThanOrEqual(3);
  });

  it('renders the Branch bar with the real Branch name and count', () => {
    load();

    const bars = element().querySelector('app-analytics-bar-chart')!;
    expect(bars.textContent).toContain('Ljmu Branch');
    expect(bars.querySelector('.bars__column')!.getAttribute('title')).toContain('594 alerts');
  });

  it('labels the confidence card as a model measurement, never as accuracy', () => {
    load();

    const card = element().querySelectorAll('.analytics__card')[1];
    expect(card.textContent).toContain('Detection confidence');
    expect(card.textContent).toContain('not human-validated');
    expect(card.textContent).toContain('83.2%');
    expect(card.textContent).not.toContain('Precision');
    expect(card.textContent).not.toContain('False Positive');
  });

  it('explains that no validated ground truth exists', () => {
    load();

    expect(element().textContent).toContain('no operator review');
  });

  it('renders the confidence bands using the thresholds the Backend supplied', () => {
    load();

    const legend = element().querySelector('app-analytics-donut-chart')!.textContent ?? '';
    expect(legend).toContain('High (≥ 75%)');
    expect(legend).toContain('Medium (50–75%)');
    expect(legend).toContain('Low (< 50%)');
  });

  it('titles the latency card for what it measures, not for operator response time', () => {
    load();

    const card = element().querySelectorAll('.analytics__card')[3];
    expect(card.textContent).toContain('Alert delivery latency');
    expect(card.textContent).toContain('Detection on the Jetson → receipt at the Backend');
    expect(card.textContent).not.toContain('Response time');
    expect(card.textContent).not.toContain('operator validation');
  });

  it('reports latency in seconds once values exceed a second, never as raw milliseconds', () => {
    load();

    const card = element().querySelectorAll('.analytics__card')[3];
    expect(card.textContent).toContain('2.5 s');
    expect(card.textContent).toContain('(seconds)');
  });

  it('reports latency in milliseconds for sub-second values', () => {
    load({
      summary: {
        ...analytics().summary,
        averageDeliveryLatencyMs: 420,
        medianDeliveryLatencyMs: 400,
        maxDeliveryLatencyMs: 800,
      },
      latencyOverTime: [{ periodStartUtc: '2026-08-17T00:00:00Z', averageMs: 420, sampleCount: 10 }],
    });

    const card = element().querySelectorAll('.analytics__card')[3];
    expect(card.textContent).toContain('420 ms');
    expect(card.textContent).toContain('(ms)');
  });

  it('discloses how many detections were excluded from the latency metric', () => {
    load();

    expect(element().textContent).toContain('1 detection was excluded');
  });

  it('states that quota-suppressed detections are excluded from the detections series', () => {
    load();

    expect(element().querySelectorAll('.analytics__card')[0].textContent).toContain(
      'Quota-suppressed detections are excluded.',
    );
  });

  // ---------------------------------------------------------------- Status footer

  it('shows only status values the platform can actually supply', () => {
    load();

    const footer = element().querySelector('.analytics__status')!.textContent ?? '';
    expect(footer).toContain('Branches in scope: 1');
    expect(footer).toContain('Cameras: 2');
    expect(footer).toContain('Jetson devices: 1');
    expect(footer).toContain('Median delivery: 2.4 s');
    expect(footer).not.toContain('Edge Nodes');
    expect(footer).not.toContain('Processing Latency');
  });

  // ---------------------------------------------------------------- Export and report

  it('requests the CSV export with the filters currently on screen', () => {
    load();

    (element().querySelectorAll('.analytics__actions .btn')[0] as HTMLButtonElement).click();
    fixture.detectChanges();

    const request = httpTesting.expectOne((req) => req.url === EXPORT_URL);
    expect(request.request.params.get('range')).toBe('last30d');
    request.flush(new Blob(['a,b\r\n'], { type: 'text/csv' }));
  });

  it('reports an export failure instead of failing silently', () => {
    load();

    (element().querySelectorAll('.analytics__actions .btn')[0] as HTMLButtonElement).click();
    fixture.detectChanges();

    httpTesting
      .expectOne((req) => req.url === EXPORT_URL)
      .flush(new Blob(), { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();

    expect(element().textContent).toContain('The CSV export could not be produced.');
  });

  it('runs a real report action rather than shipping a dead control', () => {
    load();
    const print = spyOn(window, 'print');

    (element().querySelectorAll('.analytics__actions .btn')[1] as HTMLButtonElement).click();

    expect(print).toHaveBeenCalled();
  });

  it('renders a printable provenance line describing exactly what the report covers', () => {
    load();

    const meta = element().querySelector('.analytics__print-meta')!.textContent ?? '';
    expect(meta).toContain('Last 30 days');
    expect(meta).toContain('All Branches');
    expect(meta).toContain('All detections');
    expect(meta).toContain('generated');
  });

  it('disables both actions while the page has no data to act on', () => {
    const pending = create();
    fixture.detectChanges();

    const buttons = Array.from(
      element().querySelectorAll('.analytics__actions .btn'),
    ) as HTMLButtonElement[];
    expect(buttons.every((button) => button.disabled)).toBeTrue();

    pending.flush({ success: true, data: analytics() });
  });
});
