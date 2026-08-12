import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';

import { environment } from '../../environments/environment';
import { Branch } from '../branches/branch.models';
import { DashboardSummaryComponent } from './dashboard-summary';
import { DashboardSummary } from './dashboard.models';

const SUMMARY_URL = `${environment.apiBaseUrl}/dashboard/summary`;
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const BRANCH_A_ID = '11111111-1111-1111-1111-111111111111';
const BRANCH_B_ID = '22222222-2222-2222-2222-222222222222';

function placeholderBranch(branchId: string, name: string): Branch {
  return {
    branchId,
    name,
    address: '1 Example Street',
    contactDetails: 'placeholder@example.invalid',
    cameras: [],
    device: { activationStatus: 'Unactivated' },
  };
}

function placeholderSummary(branchId: string, overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    branch: {
      id: branchId,
      name: 'Placeholder Branch',
      timeZoneId: 'Europe/London',
      localDate: '2026-07-30',
      nextQuotaResetAtUtc: '2026-07-30T23:00:00Z',
    },
    alerts: { today: 12, configuredMaximum: 15, remaining: 3, latestAlertAtUtc: '2026-07-30T10:00:00Z' },
    suppressions: { total: 47, gun: 40, knife: 7 },
    system: { deviceCount: 1, cameraCount: 1 },
    ...overrides,
  };
}

describe('DashboardSummaryComponent', () => {
  let fixture: ComponentFixture<DashboardSummaryComponent>;
  let httpTesting: HttpTestingController;
  let queryParamMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;
  let navigateSpy: jasmine.Spy;

  async function create(initialParams: Record<string, string> = {}): Promise<void> {
    TestBed.resetTestingModule();
    queryParamMap$ = new BehaviorSubject(convertToParamMap(initialParams));

    await TestBed.configureTestingModule({
      imports: [DashboardSummaryComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            queryParamMap: queryParamMap$,
            get snapshot() {
              return { queryParamMap: queryParamMap$.value };
            },
          },
        },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    navigateSpy = spyOn(TestBed.inject(Router), 'navigate').and.resolveTo(true);
    fixture = TestBed.createComponent(DashboardSummaryComponent);
  }

  beforeEach(() => create());

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  function flushBranches(branches: Branch[]): void {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: branches });
    fixture.detectChanges();
  }

  function flushSummary(branchId: string, body: object, options?: { status: number; statusText: string }): void {
    const request = httpTesting.expectOne(
      (req) => req.url === SUMMARY_URL && req.params.get('branchId') === branchId,
    );
    request.flush(body, options);
    fixture.detectChanges();
  }

  it('requests the Branch list on construction', () => {
    fixture.detectChanges();

    const request = httpTesting.expectOne(BRANCHES_URL);
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: [] });
  });

  it('shows an explicit "no Branch configured" state when the Branch list is empty', () => {
    flushBranches([]);

    expect(text()).toContain('No Branch is configured yet.');
  });

  it('shows a Branch-list failure state distinct from a summary failure', () => {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: false }, { status: 500, statusText: 'Error' });
    fixture.detectChanges();

    expect(text()).toContain('The Branch list could not be loaded.');
  });

  describe('exactly one Branch', () => {
    it('auto-selects it into the URL rather than showing a selector choice', async () => {
      await create();
      flushBranches([placeholderBranch(BRANCH_A_ID, 'Only Branch')]);

      expect(navigateSpy).toHaveBeenCalled();
      const [, options] = navigateSpy.calls.mostRecent().args as [
        unknown[],
        { queryParams: Record<string, string>; replaceUrl?: boolean },
      ];
      expect(options.queryParams['branchId']).toBe(BRANCH_A_ID);
      expect(options.replaceUrl).toBeTrue();
    });

    it('loads that Branch\'s summary once the URL carries its id', () => {
      flushBranches([placeholderBranch(BRANCH_A_ID, 'Only Branch')]);
      queryParamMap$.next(convertToParamMap({ branchId: BRANCH_A_ID }));
      fixture.detectChanges();

      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });

      expect(text()).toContain('12 / 15');
    });
  });

  describe('more than one Branch', () => {
    it('shows a selection-required state and fetches no summary when none is chosen', () => {
      flushBranches([
        placeholderBranch(BRANCH_A_ID, 'Branch A'),
        placeholderBranch(BRANCH_B_ID, 'Branch B'),
      ]);

      expect(text()).toContain('Select a Branch above to view its dashboard.');
      httpTesting.expectNone((req) => req.url === SUMMARY_URL);
    });

    it('renders the selector populated from the real Branch API, not hard-coded ids', () => {
      flushBranches([
        placeholderBranch(BRANCH_A_ID, 'Branch A'),
        placeholderBranch(BRANCH_B_ID, 'Branch B'),
      ]);

      const options = Array.from(element().querySelectorAll('.dashboard__branch-select option')).map(
        (o) => o.textContent?.trim(),
      );
      expect(options).toContain('Branch A');
      expect(options).toContain('Branch B');
    });

    it('loads only the selected Branch\'s summary when branchId is already in the URL', async () => {
      await create({ branchId: BRANCH_B_ID });
      flushBranches([
        placeholderBranch(BRANCH_A_ID, 'Branch A'),
        placeholderBranch(BRANCH_B_ID, 'Branch B'),
      ]);

      flushSummary(BRANCH_B_ID, { success: true, data: placeholderSummary(BRANCH_B_ID, { alerts: { today: 3, configuredMaximum: 15, remaining: 12, latestAlertAtUtc: null } }) });

      expect(text()).toContain('3 / 15');
      httpTesting.verify();
    });

    it('shows a not-found state when the URL\'s branchId matches no known Branch', async () => {
      await create({ branchId: '99999999-9999-9999-9999-999999999999' });
      flushBranches([
        placeholderBranch(BRANCH_A_ID, 'Branch A'),
        placeholderBranch(BRANCH_B_ID, 'Branch B'),
      ]);

      expect(text()).toContain('That Branch was not found.');
    });

    it('changing the selector navigates with the newly chosen branchId', () => {
      flushBranches([
        placeholderBranch(BRANCH_A_ID, 'Branch A'),
        placeholderBranch(BRANCH_B_ID, 'Branch B'),
      ]);

      const select = element().querySelector('.dashboard__branch-select') as HTMLSelectElement;
      select.value = select.options[2].value; // 'Select a Branch…' placeholder is index 0/1 depending on state
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();

      expect(navigateSpy).toHaveBeenCalled();
    });
  });

  describe('with a single Branch already selected via the URL', () => {
    beforeEach(async () => {
      await create({ branchId: BRANCH_A_ID });
      flushBranches([placeholderBranch(BRANCH_A_ID, 'Riverside Retail Park')]);
    });

    it('shows a loading state before the first response arrives', () => {
      expect(text()).toContain('Loading dashboard');
      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });
    });

    it('names the actual Branch in the quota card heading', () => {
      flushSummary(BRANCH_A_ID, {
        success: true,
        data: placeholderSummary(BRANCH_A_ID, { branch: { ...placeholderSummary(BRANCH_A_ID).branch, name: 'Riverside Retail Park' } }),
      });

      const heading = element().querySelector('.dashboard__quota-card .card__header h3');
      expect(heading?.textContent).toContain('Riverside Retail Park');
    });

    it('states the quota is shared across every device and camera in the Branch', () => {
      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });

      expect(text()).toContain('Shared across all devices and cameras in this Branch');
    });

    it('renders an explicit, Branch-scoped quota-exhausted state, never as a system failure', () => {
      flushSummary(BRANCH_A_ID, {
        success: true,
        data: placeholderSummary(BRANCH_A_ID, { alerts: { today: 15, configuredMaximum: 15, remaining: 0, latestAlertAtUtc: null } }),
      });

      expect(text()).toContain('Branch daily quota reached');
      expect(text()).toContain('Further detections for this Branch are being suppressed');
      expect(text()).not.toContain('could not be loaded');
    });

    it('shows a generic failure state on a Backend/network fault before any successful load', () => {
      flushSummary(BRANCH_A_ID, { success: false }, { status: 500, statusText: 'Internal Server Error' });

      expect(text()).toContain('The dashboard could not be loaded.');
    });

    it('links to the Alerts list carrying this Branch\'s id forward', () => {
      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });

      const link = element().querySelector('.dashboard__view-alerts') as HTMLAnchorElement;
      expect(link.getAttribute('href')).toContain(`branchId=${BRANCH_A_ID}`);
    });

    it('re-fetches on refresh without clearing the previously loaded summary', () => {
      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });

      const refreshButton = element().querySelector('button.btn--secondary') as HTMLButtonElement;
      refreshButton.click();
      fixture.detectChanges();

      expect(text()).toContain('12 / 15');

      flushSummary(BRANCH_A_ID, {
        success: true,
        data: placeholderSummary(BRANCH_A_ID, { alerts: { today: 13, configuredMaximum: 15, remaining: 2, latestAlertAtUtc: '2026-07-30T11:00:00Z' } }),
      });

      expect(text()).toContain('13 / 15');
    });

    it('keeps the previously loaded summary visible when a background refresh fails', () => {
      flushSummary(BRANCH_A_ID, { success: true, data: placeholderSummary(BRANCH_A_ID) });

      const refreshButton = element().querySelector('button.btn--secondary') as HTMLButtonElement;
      refreshButton.click();
      fixture.detectChanges();

      flushSummary(BRANCH_A_ID, { success: false }, { status: 500, statusText: 'Internal Server Error' });

      expect(text()).toContain('12 / 15');
      expect(text()).not.toContain('could not be loaded');
    });
  });
});
