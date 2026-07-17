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
