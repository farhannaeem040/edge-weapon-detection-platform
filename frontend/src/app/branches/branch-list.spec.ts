import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { BranchListComponent } from './branch-list';
import { Branch } from './branch.models';

// Synthetic placeholder data only.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const FIRST_BRANCH_ID = '11111111-1111-1111-1111-111111111111';
const SECOND_BRANCH_ID = '33333333-3333-3333-3333-333333333333';

function placeholderBranch(branchId: string, name: string, overrides: Partial<Branch> = {}): Branch {
  return {
    branchId,
    name,
    address: `1 Example Street, ${name}`,
    contactDetails: 'placeholder@example.invalid',
    cameras: [],
    device: { activationStatus: 'Unactivated' },
    ...overrides,
  };
}

describe('BranchListComponent', () => {
  let fixture: ComponentFixture<BranchListComponent>;
  let httpTesting: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BranchListComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(BranchListComponent);
  });

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  /** Renders the component and settles the list request with the given envelope. */
  function load(body: object, options?: { status: number; statusText: string }): void {
    fixture.detectChanges();
    const request = httpTesting.expectOne(BRANCHES_URL);
    request.flush(body, options);
    fixture.detectChanges();
  }

  it('offers a create-branch action linking to the create route', () => {
    load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'First')] });

    const create = element().querySelector('.branches__create') as HTMLAnchorElement | null;
    expect(create?.textContent?.trim()).toBe('Create branch');
    expect(create?.getAttribute('href')).toBe('/branches/new');
  });

  it('offers the create-branch action even when no branches exist yet', () => {
    load({ success: true, data: [] });

    // The Admin with no branches is precisely the one who needs to create one.
    expect(text()).toContain('No branches have been created yet.');
    expect(element().querySelector('.branches__create')).not.toBeNull();
  });

  it('requests the branch list on initialisation', () => {
    fixture.detectChanges();

    const request = httpTesting.expectOne(BRANCHES_URL);
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: [] });
  });

  it('shows a loading state while the request is in flight', () => {
    fixture.detectChanges();

    expect(text()).toContain('Loading branches');

    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [] });
  });

  it('renders each branch returned by the Backend', () => {
    load({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
        placeholderBranch(SECOND_BRANCH_ID, 'Beta Branch'),
      ],
    });

    const items = element().querySelectorAll('.branches__item');
    expect(items.length).toBe(2);
    expect(text()).toContain('Alpha Branch');
    expect(text()).toContain('Beta Branch');
  });

  it('links each branch to its detail route', () => {
    load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });

    const link = element().querySelector('.branches__link') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe(`/branches/${FIRST_BRANCH_ID}`);
  });

  it('shows an empty state when no branches exist', () => {
    load({ success: true, data: [] });

    expect(text()).toContain('No branches have been created yet.');
    expect(element().querySelectorAll('.branches__item').length).toBe(0);
  });

  it('distinguishes an empty result from a failure', () => {
    load({ success: true, data: [] });

    expect(element().querySelector('.branches__status--error')).toBeNull();
  });

  it('shows a generic failure state when the Backend fails', () => {
    load(
      { success: false, message: 'Database connection to sql-prod-01 refused.' },
      { status: 500, statusText: 'Error' },
    );

    expect(text()).toContain('Branches could not be loaded.');
    // The Backend's own error text is never surfaced (FS-02 §11).
    expect(text()).not.toContain('sql-prod-01');
  });

  it('shows the failure state when the Backend is unreachable', () => {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).error(new ProgressEvent('network error'));
    fixture.detectChanges();

    expect(text()).toContain('Branches could not be loaded.');
    expect(text()).not.toContain('Loading branches');
  });

  it('leaves the loading state on a 401 rather than spinning forever', () => {
    // The session-expiry interceptor (T-25) has already cleared the token and redirected by the
    // time this settles; the view must still reach a terminal state behind that redirect.
    load(
      { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
      { status: 401, statusText: 'Unauthorized' },
    );

    expect(text()).not.toContain('Loading branches');
  });

  it('renders the device activation status from the explicit status field', () => {
    load({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch', {
          device: { activationStatus: 'Activated', deviceId: '44444444-4444-4444-4444-444444444444' },
        }),
        placeholderBranch(SECOND_BRANCH_ID, 'Beta Branch'),
      ],
    });

    const badges = element().querySelectorAll('.branches__status-badge');
    expect(badges[0].textContent).toContain('Activated');
    expect(badges[1].textContent).toContain('Unactivated');
  });

  it('renders the status through the reusable badge component (T-29)', () => {
    load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });

    // One status treatment across the list and the detail, not two that happen to agree.
    const badges = element().querySelectorAll('app-device-status-badge');
    expect(badges.length).toBe(1);
    expect(badges[0].querySelector('.device-status__label')?.textContent?.trim()).toBe(
      'Unactivated',
    );
  });

  it('shows Unactivated even when the payload contradicts itself with a Device ID', () => {
    // `activationStatus` is the explicit field. A `deviceId` alongside `Unactivated` is not a signal
    // to reinterpret it — the status wins, and no Device ID reaches the row.
    load({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch', {
          device: {
            activationStatus: 'Unactivated',
            deviceId: '44444444-4444-4444-4444-444444444444',
          },
        }),
      ],
    });

    const badge = element().querySelector('.branches__status-badge');
    expect(badge?.textContent).toContain('Unactivated');
    expect(text()).not.toContain('44444444-4444-4444-4444-444444444444');
  });

  it('shows Activated even when the payload contradicts itself with no Device ID', () => {
    load({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch', {
          device: { activationStatus: 'Activated' },
        }),
      ],
    });

    const badge = element().querySelector('.branches__status-badge');
    expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe('Activated');
  });

  it('shows a neutral Unknown badge for a status outside the contract', () => {
    load({
      success: true,
      data: [
        {
          ...placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
          device: { activationStatus: 'PLACEHOLDER-UNEXPECTED-STATUS' },
        },
      ],
    });

    const badge = element().querySelector('.branches__status-badge');
    expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe('Unknown');
    expect(text()).not.toContain('PLACEHOLDER-UNEXPECTED-STATUS');
  });

  it('loads the latest status from the Backend on each render rather than from a cache', () => {
    // What a refresh does: the status shown is whatever the Branch API last said, so a Device
    // activated out-of-band appears on reload with no polling and no client-side invalidation.
    load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });
    expect(element().querySelector('.branches__status-badge')?.textContent).toContain(
      'Unactivated',
    );

    const reloaded = TestBed.createComponent(BranchListComponent);
    reloaded.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch', {
          device: {
            activationStatus: 'Activated',
            deviceId: '44444444-4444-4444-4444-444444444444',
          },
        }),
      ],
    });
    reloaded.detectChanges();

    const badge = (reloaded.nativeElement as HTMLElement).querySelector('.branches__status-badge');
    expect(badge?.textContent).toContain('Activated');
  });

  it('stores no activation status in browser storage', () => {
    // Cleared first so this asserts about the list, not about what a neighbouring spec left behind.
    sessionStorage.clear();
    localStorage.clear();

    load({
      success: true,
      data: [
        placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch', {
          device: {
            activationStatus: 'Activated',
            deviceId: '44444444-4444-4444-4444-444444444444',
          },
        }),
      ],
    });

    // Stale status must not survive a reload; the Branch API is the only source.
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });

  it('renders no secrets, keys, or internal identifiers', () => {
    // Members the read contract does not define. If a view ever rendered an unmodelled member, this
    // is what would catch it.
    load({
      success: true,
      data: [
        {
          ...placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
          activationKey: 'PLACEHOLDER-ACTIVATION-KEY',
          deviceRecordId: 'PLACEHOLDER-DEVICE-RECORD-ID',
          protectedSharedSecret: 'PLACEHOLDER-PROTECTED-SECRET',
        },
      ],
    });

    const rendered = text();
    expect(rendered).not.toContain('PLACEHOLDER-ACTIVATION-KEY');
    expect(rendered).not.toContain('PLACEHOLDER-DEVICE-RECORD-ID');
    expect(rendered).not.toContain('PLACEHOLDER-PROTECTED-SECRET');
  });
});
