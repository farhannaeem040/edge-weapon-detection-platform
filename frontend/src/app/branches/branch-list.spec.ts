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

  describe('edit action (T-45)', () => {
    it('renders an edit link beside every branch, pointing at that branch edit route', () => {
      load({
        success: true,
        data: [
          placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
          placeholderBranch(SECOND_BRANCH_ID, 'Beta Branch'),
        ],
      });

      const editLinks = element().querySelectorAll('.branches__edit');
      expect(editLinks.length).toBe(2);
      expect(editLinks[0].getAttribute('href')).toBe(`/branches/${FIRST_BRANCH_ID}/edit`);
      expect(editLinks[1].getAttribute('href')).toBe(`/branches/${SECOND_BRANCH_ID}/edit`);
    });

    it('gives the edit link an aria-label and title naming the branch', () => {
      load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });

      const editLink = element().querySelector('.branches__edit') as HTMLAnchorElement;
      expect(editLink.getAttribute('aria-label')).toBe('Edit branch Alpha Branch');
      expect(editLink.getAttribute('title')).toBe('Edit branch Alpha Branch');
    });

    it('renders the edit icon as decorative, leaving the label to the link', () => {
      load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });

      const icon = element().querySelector('.branches__edit svg');
      expect(icon?.getAttribute('aria-hidden')).toBe('true');
      expect(icon?.getAttribute('focusable')).toBe('false');
    });

    it('renders the edit control as a real, keyboard-focusable link (an anchor with href)', () => {
      load({ success: true, data: [placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch')] });

      const editLink = element().querySelector('.branches__edit') as HTMLAnchorElement;
      // An <a href> is natively in the tab order — no tabindex needed — unlike a bare clickable span.
      expect(editLink.tagName).toBe('A');
      expect(editLink.hasAttribute('href')).toBeTrue();
    });
  });

  describe('delete action (T-46)', () => {
    function loadTwo(): void {
      load({
        success: true,
        data: [
          placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
          placeholderBranch(SECOND_BRANCH_ID, 'Beta Branch'),
        ],
      });
    }

    function deleteUrl(branchId: string): string {
      return `${BRANCHES_URL}/${branchId}`;
    }

    it('renders a delete button beside every branch, naming it in the aria-label and title', () => {
      loadTwo();

      const deletes = element().querySelectorAll('.branches__delete');
      expect(deletes.length).toBe(2);
      expect(deletes[0].getAttribute('aria-label')).toBe('Delete branch Alpha Branch');
      expect(deletes[0].getAttribute('title')).toBe('Delete branch Alpha Branch');
    });

    it('renders the delete control as a real button, not the branch name link', () => {
      loadTwo();

      const del = element().querySelector('.branches__delete') as HTMLElement;
      // A native <button> is keyboard-operable and in the tab order; the destructive action never
      // rides on the row's own name link (FS-03 §6.1).
      expect(del.tagName).toBe('BUTTON');
      expect(element().querySelector('.branches__link')?.classList.contains('branches__delete'))
        .toBeFalse();
    });

    it('opens a confirmation and issues no request on the first click', () => {
      loadTwo();

      (element().querySelector('.branches__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      expect(element().querySelector('app-branch-delete-confirm')).not.toBeNull();
      // afterEach's verify() asserts nothing was sent by merely opening the confirmation.
    });

    it('sends no request when the confirmation is cancelled', () => {
      loadTwo();
      (element().querySelector('.branches__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      (element().querySelector('.delete-confirm__cancel') as HTMLButtonElement).click();
      fixture.detectChanges();

      expect(element().querySelector('app-branch-delete-confirm')).toBeNull();
      // verify() confirms the cancel path made no HTTP call (FS-03 §6.3, AC-13).
    });

    it('calls the exact delete endpoint once on confirm and removes the branch from the list', () => {
      loadTwo();
      (element().querySelectorAll('.branches__delete')[0] as HTMLButtonElement).click();
      fixture.detectChanges();

      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      const requests = httpTesting.match(deleteUrl(FIRST_BRANCH_ID));
      expect(requests.length).toBe(1);
      expect(requests[0].request.method).toBe('DELETE');
      requests[0].flush({ success: true, message: 'Branch deleted.' });
      fixture.detectChanges();

      // The deleted branch's row is gone; the other remains. (The confirmation feedback still names
      // the deleted branch, so this checks the list rows, not the whole page text.)
      const links = Array.from(element().querySelectorAll('.branches__link')).map((a) =>
        a.textContent?.trim(),
      );
      expect(links).toEqual(['Beta Branch']);
      expect(text()).toContain('was deleted');
    });

    it('prevents a duplicate delete request from a double confirm', () => {
      loadTwo();
      (element().querySelectorAll('.branches__delete')[0] as HTMLButtonElement).click();
      fixture.detectChanges();

      const confirm = element().querySelector('.delete-confirm__delete') as HTMLButtonElement;
      confirm.click();
      fixture.detectChanges();
      confirm.click();
      fixture.detectChanges();

      const requests = httpTesting.match(deleteUrl(FIRST_BRANCH_ID));
      expect(requests.length).toBe(1);
      requests[0].flush({ success: true, message: 'Branch deleted.' });
    });

    it('treats a 404 as the branch already being gone and removes it', () => {
      loadTwo();
      (element().querySelectorAll('.branches__delete')[0] as HTMLButtonElement).click();
      fixture.detectChanges();
      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      httpTesting
        .expectOne(deleteUrl(FIRST_BRANCH_ID))
        .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
      fixture.detectChanges();

      const links = Array.from(element().querySelectorAll('.branches__link')).map((a) =>
        a.textContent?.trim(),
      );
      expect(links).toEqual(['Beta Branch']);
    });

    it('shows a generic failure and keeps the branch on a server error', () => {
      loadTwo();
      (element().querySelectorAll('.branches__delete')[0] as HTMLButtonElement).click();
      fixture.detectChanges();
      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      httpTesting
        .expectOne(deleteUrl(FIRST_BRANCH_ID))
        .flush({ success: false, message: 'FK constraint on Devices failed.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be deleted.');
      expect(text()).not.toContain('FK constraint');
      // The branch is untouched in the list.
      expect(text()).toContain('Alpha Branch');
    });

    it('reaches a terminal state on a 401 rather than deleting from the UI', () => {
      loadTwo();
      (element().querySelectorAll('.branches__delete')[0] as HTMLButtonElement).click();
      fixture.detectChanges();
      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      // The session-expiry interceptor (T-25) handles the redirect; the view still settles.
      httpTesting
        .expectOne(deleteUrl(FIRST_BRANCH_ID))
        .flush({ success: false, errorCode: 'UNAUTHORIZED' }, { status: 401, statusText: 'Unauthorized' });
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be deleted.');
      expect(text()).toContain('Alpha Branch');
    });

    it('leaves the edit link working alongside delete', () => {
      loadTwo();

      const edit = element().querySelector('.branches__edit') as HTMLAnchorElement;
      expect(edit.getAttribute('href')).toBe(`/branches/${FIRST_BRANCH_ID}/edit`);
    });

    it('renders no secret or internal identifier in the confirmation', () => {
      load({
        success: true,
        data: [
          {
            ...placeholderBranch(FIRST_BRANCH_ID, 'Alpha Branch'),
            activationKey: 'PLACEHOLDER-KEY',
            deviceRecordId: 'PLACEHOLDER-RECORD-ID',
          } as Branch,
        ],
      });
      (element().querySelector('.branches__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      const rendered = text();
      expect(rendered).not.toContain('PLACEHOLDER-KEY');
      expect(rendered).not.toContain('PLACEHOLDER-RECORD-ID');
    });
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
