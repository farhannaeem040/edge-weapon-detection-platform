import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';

import { environment } from '../../environments/environment';
import { AlertListComponent } from './alert-list';
import { AlertListItem } from './alert.models';

const ALERTS_URL = `${environment.apiBaseUrl}/alerts`;
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;

function placeholderItem(overrides: Partial<AlertListItem> = {}): AlertListItem {
  return {
    alertId: '11111111-1111-1111-1111-111111111111',
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
    ...overrides,
  };
}

describe('AlertListComponent', () => {
  let fixture: ComponentFixture<AlertListComponent>;
  let httpTesting: HttpTestingController;
  let queryParamMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  let navigateSpy: jasmine.Spy;

  async function create(initialParams: Record<string, string> = {}): Promise<void> {
    TestBed.resetTestingModule();
    queryParamMap$ = new BehaviorSubject(convertToParamMap(initialParams));

    await TestBed.configureTestingModule({
      imports: [AlertListComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: { queryParamMap: queryParamMap$, snapshot: { queryParamMap: queryParamMap$.value } },
        },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    navigateSpy = spyOn(TestBed.inject(Router), 'navigate').and.resolveTo(true);
    fixture = TestBed.createComponent(AlertListComponent);
  }

  beforeEach(() => create());

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  /** The Branch-filter dropdown's own data source — flushed with an empty list unless a test cares. */
  function flushBranches(branches: object[] = []): void {
    httpTesting.expectOne((req) => req.url === BRANCHES_URL).flush({ success: true, data: branches });
  }

  function load(body: object, options?: { status: number; statusText: string }): void {
    fixture.detectChanges();
    flushBranches();
    const request = httpTesting.expectOne((req) => req.url === ALERTS_URL);
    request.flush(body, options);
    fixture.detectChanges();
  }

  it('requests Alerts with the default filter on initialisation', () => {
    fixture.detectChanges();
    flushBranches();

    const request = httpTesting.expectOne((req) => req.url === ALERTS_URL);
    expect(request.request.params.get('page')).toBe('1');
    expect(request.request.params.get('sortBy')).toBe('detectedAtUtc');
    expect(request.request.params.get('sortDescending')).toBe('true');
    request.flush({ success: true, data: { items: [], page: 1, pageSize: 25, totalCount: 0, totalPages: 1 } });
  });

  it('shows a loading state while the request is in flight', () => {
    fixture.detectChanges();
    flushBranches();
    expect(text()).toContain('Loading Alerts');
    httpTesting
      .expectOne((req) => req.url === ALERTS_URL)
      .flush({ success: true, data: { items: [], page: 1, pageSize: 25, totalCount: 0, totalPages: 1 } });
  });


  it('shows an empty state when no Alerts match the filter', () => {
    load({ success: true, data: { items: [], page: 1, pageSize: 25, totalCount: 0, totalPages: 1 } });

    expect(text()).toContain('No Alerts match the current filters.');
  });

  it('shows a generic failure state on a Backend/network fault', () => {
    load({ success: false }, { status: 500, statusText: 'Internal Server Error' });

    expect(text()).toContain('Alerts could not be loaded.');
  });

  it('renders each Alert with its weapon-class and status badges', () => {
    load({
      success: true,
      data: { items: [placeholderItem()], page: 1, pageSize: 25, totalCount: 1, totalPages: 1 },
    });

    expect(element().querySelector('app-weapon-class-badge')).not.toBeNull();
    expect(element().querySelector('app-alert-status-badge')).not.toBeNull();
    expect(text()).toContain('Front Entrance');
  });

  it('shows pagination summary and disables Previous on the first page', () => {
    load({
      success: true,
      data: { items: [placeholderItem()], page: 1, pageSize: 25, totalCount: 40, totalPages: 2 },
    });

    expect(text()).toContain('Page 1 of 2');
    expect(text()).toContain('40 Alerts');
    const previous = element().querySelectorAll('.alerts__pagination button')[0] as HTMLButtonElement;
    expect(previous.disabled).toBeTrue();
  });

  it('navigates to the next page when Next is clicked', () => {
    load({
      success: true,
      data: { items: [placeholderItem()], page: 1, pageSize: 25, totalCount: 40, totalPages: 2 },
    });

    const next = element().querySelectorAll('.alerts__pagination button')[1] as HTMLButtonElement;
    next.click();

    expect(navigateSpy).toHaveBeenCalled();
    const [, options] = navigateSpy.calls.mostRecent().args as [unknown[], { queryParams: Record<string, string | null> }];
    expect(options.queryParams['page']).toBe('2');
  });

  it('re-fetches with the new query params when the URL changes (back/forward navigation)', () => {
    load({ success: true, data: { items: [], page: 1, pageSize: 25, totalCount: 0, totalPages: 1 } });

    queryParamMap$.next(convertToParamMap({ page: '2' }));
    fixture.detectChanges();

    const request = httpTesting.expectOne((req) => req.url === ALERTS_URL);
    expect(request.request.params.get('page')).toBe('2');
    request.flush({ success: true, data: { items: [], page: 2, pageSize: 25, totalCount: 0, totalPages: 1 } });
  });

  describe('Branch filter (manual-review Correction 2)', () => {
    const BRANCH_A = {
      branchId: '55555555-5555-5555-5555-555555555555',
      name: 'Branch A',
      address: '1 A Street',
      contactDetails: 'a@example.invalid',
      cameras: [],
      device: { activationStatus: 'Unactivated' },
    };
    const BRANCH_B = {
      branchId: '66666666-6666-6666-6666-666666666666',
      name: 'Branch B',
      address: '1 B Street',
      contactDetails: 'b@example.invalid',
      cameras: [],
      device: { activationStatus: 'Unactivated' },
    };

    function loadWithBranches(): void {
      fixture.detectChanges();
      flushBranches([BRANCH_A, BRANCH_B]);
      httpTesting
        .expectOne((req) => req.url === ALERTS_URL)
        .flush({ success: true, data: { items: [], page: 1, pageSize: 25, totalCount: 0, totalPages: 1 } });
      fixture.detectChanges();
    }

    it('populates the dropdown from the real Branch API with an "All Branches" option, never hard-coded ids', () => {
      loadWithBranches();

      const options = Array.from(
        element().querySelectorAll('#alerts-filter-branch option'),
      ).map((o) => o.textContent?.trim());
      expect(options).toEqual(['All Branches', 'Branch A', 'Branch B']);
    });

    it('sends the selected branchId as a query param when the filter is applied', () => {
      loadWithBranches();

      const select = element().querySelector('#alerts-filter-branch') as HTMLSelectElement;
      select.value = BRANCH_A.branchId;
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();

      (element().querySelector('form') as HTMLFormElement).dispatchEvent(new Event('submit'));

      expect(navigateSpy).toHaveBeenCalled();
      const [, options] = navigateSpy.calls.mostRecent().args as [
        unknown[],
        { queryParams: Record<string, string | null> },
      ];
      expect(options.queryParams['branchId']).toBe(BRANCH_A.branchId);
      // Changing any filter resets pagination to page 1.
      expect(options.queryParams['page']).toBeNull();
    });

    it('restores the selected Branch from the URL on load', async () => {
      await create({ branchId: BRANCH_B.branchId });
      loadWithBranches();

      const select = element().querySelector('#alerts-filter-branch') as HTMLSelectElement;
      expect(select.value).toBe(BRANCH_B.branchId);
    });

    it('Clear removes the branchId query param', () => {
      loadWithBranches();

      (element().querySelector('.alerts__filters-actions .btn--ghost') as HTMLButtonElement).click();

      expect(navigateSpy).toHaveBeenCalled();
      const [, options] = navigateSpy.calls.mostRecent().args as [
        unknown[],
        { queryParams: Record<string, string | null> },
      ];
      expect(options.queryParams['branchId']).toBeNull();
    });
  });
});
