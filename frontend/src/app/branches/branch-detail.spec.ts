import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { BranchDetailComponent } from './branch-detail';
import { Branch } from './branch.models';

// Synthetic placeholder data only.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const PLACEHOLDER_BRANCH_ID = '11111111-1111-1111-1111-111111111111';
const PLACEHOLDER_DEVICE_ID = '44444444-4444-4444-4444-444444444444';

/** The regeneration route, addressed by the **branch** id — the Backend's contract (FS-02 §1.3). */
const REGENERATE_URL = `${environment.apiBaseUrl}/devices/${PLACEHOLDER_BRANCH_ID}/activation-key/regenerate`;

/** A synthetic stand-in for a regenerated key, in the `keyId.secret` shape. Never a real key. */
const PLACEHOLDER_REGENERATED_KEY = 'placeholdernewkeyid.placeholdernewsecretvalue';

/**
 * The redacted form the Backend emits for a camera whose stored URL embedded credentials: its
 * `RtspUrlSanitizer` replaces the userinfo span with `***` before the value leaves the Backend.
 */
const REDACTED_RTSP_URL = 'rtsp://***@camera.example.invalid:554/stream1';

function placeholderBranch(overrides: Partial<Branch> = {}): Branch {
  return {
    branchId: PLACEHOLDER_BRANCH_ID,
    name: 'Alpha Branch',
    address: '1 Example Street, Placeholder City',
    contactDetails: 'placeholder@example.invalid',
    cameras: [
      {
        cameraId: '22222222-2222-2222-2222-222222222222',
        name: 'Front Entrance',
        rtspUrl: REDACTED_RTSP_URL,
        enabled: true,
      },
    ],
    device: { activationStatus: 'Unactivated' },
    ...overrides,
  };
}

describe('BranchDetailComponent', () => {
  let fixture: ComponentFixture<BranchDetailComponent>;
  let httpTesting: HttpTestingController;

  /** Builds the component with `branchId` on the route, as the router would supply it. */
  async function createWithRouteParam(branchId: string | null): Promise<void> {
    TestBed.resetTestingModule();

    await TestBed.configureTestingModule({
      imports: [BranchDetailComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: convertToParamMap(branchId === null ? {} : { branchId }),
            },
          },
        },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(BranchDetailComponent);
  }

  beforeEach(() => createWithRouteParam(PLACEHOLDER_BRANCH_ID));

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  function load(body: object, options?: { status: number; statusText: string }): void {
    fixture.detectChanges();
    httpTesting.expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`).flush(body, options);
    fixture.detectChanges();
  }

  it('loads the branch named by the route parameter', () => {
    fixture.detectChanges();

    const request = httpTesting.expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`);
    expect(request.request.method).toBe('GET');
    request.flush({ success: true, data: placeholderBranch() });
  });

  it('shows a loading state while the request is in flight', () => {
    fixture.detectChanges();

    expect(text()).toContain('Loading branch');

    httpTesting
      .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
      .flush({ success: true, data: placeholderBranch() });
  });

  it('renders the branch name, address, and contact details', () => {
    load({ success: true, data: placeholderBranch() });

    expect(text()).toContain('Alpha Branch');
    expect(element().querySelector('.branch__address')?.textContent).toContain('1 Example Street');
    expect(element().querySelector('.branch__contact')?.textContent).toContain(
      'placeholder@example.invalid',
    );
  });

  it('renders every configured camera', () => {
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: '22222222-2222-2222-2222-222222222222',
            name: 'Front Entrance',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
          },
          {
            cameraId: '55555555-5555-5555-5555-555555555555',
            name: 'Rear Exit',
            rtspUrl: 'rtsp://camera.example.invalid:554/stream2',
            enabled: false,
          },
        ],
      }),
    });

    const cameras = element().querySelectorAll('.branch__camera');
    expect(cameras.length).toBe(2);
    expect(text()).toContain('Front Entrance');
    expect(text()).toContain('Rear Exit');
  });

  it('renders each camera enabled state', () => {
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: '55555555-5555-5555-5555-555555555555',
            name: 'Rear Exit',
            rtspUrl: 'rtsp://camera.example.invalid:554/stream2',
            enabled: false,
          },
        ],
      }),
    });

    expect(element().querySelector('.branch__camera-enabled')?.textContent).toContain('Disabled');
  });

  it('shows an empty state when a branch has no cameras', () => {
    load({ success: true, data: placeholderBranch({ cameras: [] }) });

    expect(text()).toContain('No cameras are configured for this branch.');
  });

  it('renders the Backend-provided RTSP value verbatim and redacts nothing itself', () => {
    load({ success: true, data: placeholderBranch() });

    // The Backend already redacted the credential span; the view displays exactly what arrived.
    expect(element().querySelector('.branch__camera-url')?.textContent?.trim()).toBe(
      REDACTED_RTSP_URL,
    );
  });

  it('never renders a credential-bearing RTSP URL, because the Backend never sends one', () => {
    load({ success: true, data: placeholderBranch() });

    const rendered = text();
    expect(rendered).toContain('***');
    // No userinfo credentials can appear: the redacted form is all that exists client-side.
    expect(rendered).not.toContain('placeholder-camera-password');
  });

  describe('device activation status', () => {
    it('renders Unactivated for an unactivated device', () => {
      load({ success: true, data: placeholderBranch({ device: { activationStatus: 'Unactivated' } }) });

      expect(element().querySelector('.branch__device-status')?.textContent).toContain(
        'Unactivated',
      );
    });

    it('renders no Device ID for an unactivated device', () => {
      load({ success: true, data: placeholderBranch({ device: { activationStatus: 'Unactivated' } }) });

      // FS-02 AC-7: not an empty field, not a placeholder identifier — an explicit "not assigned".
      expect(element().querySelector('.branch__device-id')).toBeNull();
      expect(element().querySelector('.branch__device-id-absent')?.textContent).toContain(
        'not yet assigned',
      );
    });

    it('renders Activated for an activated device', () => {
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      expect(element().querySelector('.branch__device-status')?.textContent).toContain('Activated');
    });

    it('renders the public Device ID for an activated device', () => {
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      expect(element().querySelector('.branch__device-id')?.textContent).toContain(
        PLACEHOLDER_DEVICE_ID,
      );
      expect(element().querySelector('.branch__device-id-absent')).toBeNull();
    });

    it('does not infer activation from the presence of a Device ID', () => {
      // A contradictory payload the Backend would never emit: status is the only field trusted, so
      // an Unactivated device shows no Device ID even when one is somehow present.
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Unactivated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      expect(text()).not.toContain(PLACEHOLDER_DEVICE_ID);
      expect(element().querySelector('.branch__device-id-absent')).not.toBeNull();
    });

    it('renders the status through the reusable badge component (T-29)', () => {
      load({ success: true, data: placeholderBranch() });

      // The same component the list uses — one status treatment, not two that agree by accident.
      const badge = element().querySelector('app-device-status-badge');
      expect(badge).not.toBeNull();
      expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe(
        'Unactivated',
      );
    });

    it('shows the badge as Unactivated when the payload contradicts itself with a Device ID', () => {
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Unactivated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      const badge = element().querySelector('.branch__device-status');
      expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe(
        'Unactivated',
      );
    });

    it('shows the badge as Activated when the payload contradicts itself with no Device ID', () => {
      // The mirror case: the status is trusted, so the badge reads Activated — and the Device ID
      // section still renders no fabricated identifier, because there is none to render.
      load({ success: true, data: placeholderBranch({ device: { activationStatus: 'Activated' } }) });

      const badge = element().querySelector('.branch__device-status');
      expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe('Activated');
      expect(element().querySelector('.branch__device-id')).toBeNull();
      expect(element().querySelector('.branch__device-id-absent')).not.toBeNull();
    });

    it('shows a neutral Unknown badge for a status outside the contract', () => {
      load({
        success: true,
        data: {
          ...placeholderBranch(),
          device: { activationStatus: 'PLACEHOLDER-UNEXPECTED-STATUS' },
        },
      });

      const badge = element().querySelector('.branch__device-status');
      expect(badge?.querySelector('.device-status__label')?.textContent?.trim()).toBe('Unknown');
      expect(text()).not.toContain('PLACEHOLDER-UNEXPECTED-STATUS');
      // Unknown is not Activated: no Device ID is claimed on the strength of a value we cannot read.
      expect(element().querySelector('.branch__device-id')).toBeNull();
    });

    it('gives the status accessible text beyond the bare label', () => {
      load({ success: true, data: placeholderBranch() });

      expect(element().querySelector('.device-status__context')?.textContent).toContain(
        'Device status:',
      );
      expect(text()).toContain('has not been activated yet');
    });

    it('shows the latest status when the view is revisited after activation', async () => {
      // The refresh path FS-02 AC-7 asks for: a Device activated out-of-band via the Backend's own
      // endpoint appears on the next load. No polling, no cache to invalidate.
      load({ success: true, data: placeholderBranch() });
      expect(element().querySelector('.branch__device-status')?.textContent).toContain(
        'Unactivated',
      );

      await createWithRouteParam(PLACEHOLDER_BRANCH_ID);
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      expect(element().querySelector('.branch__device-status')?.textContent).toContain('Activated');
      expect(element().querySelector('.branch__device-id')?.textContent).toContain(
        PLACEHOLDER_DEVICE_ID,
      );
    });

    it('stores no activation status or Device ID in browser storage', () => {
      // Cleared first so this asserts about the detail view, not about a neighbouring spec.
      sessionStorage.clear();
      localStorage.clear();

      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      expect(sessionStorage.length).toBe(0);
      expect(localStorage.length).toBe(0);
    });
  });

  describe('Activation Key regeneration (T-28)', () => {
    /** Loads the branch, then opens the confirmation — the state most tests below start from. */
    function openConfirmation(branch: Branch = placeholderBranch()): void {
      load({ success: true, data: branch });
      query('.branch__regenerate')?.click();
      fixture.detectChanges();
    }

    function query(selector: string): HTMLElement | null {
      return element().querySelector(selector) as HTMLElement | null;
    }

    /** Answers the in-flight regeneration and settles the view. */
    function flushRegeneration(body: object, options?: { status: number; statusText: string }): void {
      httpTesting.expectOne(REGENERATE_URL).flush(body, options);
      fixture.detectChanges();
    }

    function succeed(): void {
      flushRegeneration({ success: true, data: { activationKey: PLACEHOLDER_REGENERATED_KEY } });
    }

    it('renders the regeneration action on a loaded branch', () => {
      load({ success: true, data: placeholderBranch() });

      expect(query('.branch__regenerate')?.textContent).toContain('Regenerate Activation Key');
    });

    it('offers the action for an unactivated Device', () => {
      load({ success: true, data: placeholderBranch({ device: { activationStatus: 'Unactivated' } }) });

      // The never-consumed case (FS-02 §15 T-03) — the reason the endpoint is addressed by branch id.
      expect(query('.branch__regenerate')).not.toBeNull();
    });

    it('offers the action for an activated Device', () => {
      load({
        success: true,
        data: placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
      });

      // FS-02 §5.3 restricts regeneration to neither state: the key is invalidated "regardless of its
      // consumption state", which is exactly the activated/reactivation case (§15 T-09, AC-5).
      expect(query('.branch__regenerate')).not.toBeNull();
    });

    it('shows no confirmation until the action is selected', () => {
      load({ success: true, data: placeholderBranch() });

      expect(query('.branch__confirm')).toBeNull();
    });

    it('shows the confirmation when the action is selected, and sends nothing yet', () => {
      openConfirmation();

      expect(query('.branch__confirm')).not.toBeNull();
      expect(text()).toContain("Regenerate this branch's Activation Key?");
      // verify() asserts that selecting the action issued no request of its own.
    });

    it('explains what regeneration does before the Admin confirms', () => {
      openConfirmation();

      const rendered = text();
      expect(rendered).toContain('stops working immediately');
      expect(rendered).toContain('A new Activation Key is generated');
      expect(rendered).toContain('public Device ID does not change');
      expect(rendered).toContain('is not deactivated');
      expect(rendered).toContain('next activation or reactivation');
    });

    it('does not reveal whether the current key had been used', () => {
      openConfirmation();

      const rendered = text();
      expect(rendered).not.toContain('consumed');
      expect(rendered).not.toContain('unconsumed');
    });

    it('issues no request when the Admin cancels, and keeps the branch view', () => {
      openConfirmation();

      query('.branch__confirm-cancel')?.click();
      fixture.detectChanges();

      // verify() in afterEach asserts no regeneration request was ever issued.
      expect(query('.branch__confirm')).toBeNull();
      expect(query('.branch__regenerate')).not.toBeNull();
      expect(text()).toContain('Alpha Branch');
    });

    it('creates no key state when the Admin cancels', () => {
      openConfirmation();

      query('.branch__confirm-cancel')?.click();
      fixture.detectChanges();

      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
    });

    it('calls the exact endpoint once when the Admin confirms', () => {
      openConfirmation();

      query('.branch__confirm-regenerate')?.click();

      const request = httpTesting.expectOne(REGENERATE_URL);
      expect(request.request.method).toBe('POST');
      request.flush({ success: true, data: { activationKey: PLACEHOLDER_REGENERATED_KEY } });
      fixture.detectChanges();
    });

    it('prevents a duplicate request while one is in flight', () => {
      openConfirmation();

      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // A second click must not mint a second key: the first would be invalidated before the Admin
      // ever saw it, and it can never be recovered.
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // expectOne() asserts exactly one request exists for the URL.
      httpTesting
        .expectOne(REGENERATE_URL)
        .flush({ success: true, data: { activationKey: PLACEHOLDER_REGENERATED_KEY } });
      fixture.detectChanges();
    });

    it('shows a loading state while the request is in flight', () => {
      openConfirmation();

      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      expect(text()).toContain('Regenerating…');
      succeed();
    });

    it('renders no key before a successful response', () => {
      openConfirmation();

      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // In flight: no key exists yet, so none is rendered.
      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);

      succeed();
    });

    it('displays the new key through ActivationKeyDisplayComponent on success', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      // Reused, not reimplemented: one disclosure treatment for both T-27 and T-28.
      expect(query('app-activation-key-display')).not.toBeNull();
      expect(query('.activation-key__value')?.textContent?.trim()).toBe(PLACEHOLDER_REGENERATED_KEY);
    });

    it('states that the previous key is no longer valid', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      expect(text()).toContain('no longer valid');
      expect(text()).toContain('shown once');
    });

    it('does not navigate away or re-read the branch on success', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      // The Admin stays on the branch, with the key on screen for as long as they need it. verify()
      // asserts no further request — the key is never re-fetched, and nothing re-reads the branch
      // out from under the disclosure.
      expect(text()).toContain('Alpha Branch');
    });

    it('requires an explicit copy action for the regenerated key', async () => {
      const writeText = spyOn(navigator.clipboard, 'writeText').and.resolveTo();

      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      expect(writeText).not.toHaveBeenCalled();

      query('.activation-key__copy')?.click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(writeText).toHaveBeenCalledOnceWith(PLACEHOLDER_REGENERATED_KEY);
      expect(text()).toContain('copied to the clipboard');
    });

    it('reports a failed copy of the regenerated key', async () => {
      spyOn(navigator.clipboard, 'writeText').and.rejectWith(new Error('Clipboard unavailable.'));

      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      query('.activation-key__copy')?.click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(text()).toContain('The key could not be copied.');
      expect(text()).not.toContain('Clipboard unavailable.');
    });

    it('clears the key when the Admin completes the flow', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      query('.activation-key__continue')?.click();
      fixture.detectChanges();

      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      // Back to the ordinary branch view, with the action available again.
      expect(text()).toContain('Alpha Branch');
      expect(query('.branch__regenerate')).not.toBeNull();
    });

    it('cannot recover the key after the component is destroyed and recreated', async () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();
      expect(text()).toContain(PLACEHOLDER_REGENERATED_KEY);

      fixture.destroy();

      // A rebuild is what a refresh or a re-navigation produces: a fresh component that reads the
      // branch and nothing else. There is no endpoint that would return the key, and verify()
      // asserts none is called — the only request is the ordinary branch read.
      await createWithRouteParam(PLACEHOLDER_BRANCH_ID);
      load({ success: true, data: placeholderBranch() });

      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(query('app-activation-key-display')).toBeNull();
      expect(query('.branch__regenerate')).not.toBeNull();
    });

    it('writes the regenerated key to no storage, cookie, or URL', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      expect(JSON.stringify(sessionStorage)).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(JSON.stringify(localStorage)).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(document.cookie).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(window.location.href).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(window.location.search).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(JSON.stringify(history.state ?? {})).not.toContain(PLACEHOLDER_REGENERATED_KEY);

      // No IndexedDB database is opened at all — the key has no persistent home to be written to.
      expect(query('app-activation-key-display')).not.toBeNull();
    });

    it('puts the regenerated key in no link', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      const anchors: HTMLAnchorElement[] = Array.from(element().querySelectorAll('a'));
      expect(anchors.length).toBeGreaterThan(0);
      for (const anchor of anchors) {
        expect(anchor.getAttribute('href') ?? '').not.toContain(PLACEHOLDER_REGENERATED_KEY);
      }
    });

    it('opens IndexedDB for nothing during the flow', () => {
      const open = spyOn(indexedDB, 'open').and.callThrough();

      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      expect(open).not.toHaveBeenCalled();
    });

    it('shows a generic error when regeneration fails', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      flushRegeneration(
        { success: false, message: 'Deadlock on ActivationKeys at sql-prod-01.' },
        { status: 500, statusText: 'Error' },
      );

      expect(text()).toContain('The Activation Key could not be regenerated.');
      // No Backend text, no SQL/lock detail, no status code (FS-02 §11).
      expect(text()).not.toContain('sql-prod-01');
      expect(text()).not.toContain('Deadlock');
    });

    it('displays no key after a failed regeneration', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      flushRegeneration({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });

      // Neither fabricated nor stale: the disclosure renders only from a successful response.
      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(text()).not.toContain('.placeholder');
    });

    it('does not leave a stale key on screen when a later attempt fails', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed();

      query('.activation-key__continue')?.click();
      fixture.detectChanges();

      query('.branch__regenerate')?.click();
      fixture.detectChanges();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      flushRegeneration({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });

      expect(text()).toContain('The Activation Key could not be regenerated.');
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
    });

    it('shows a not-found message when the regeneration target is unknown', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      flushRegeneration(
        { success: false, message: 'Device not found.', errorCode: 'NOT_FOUND' },
        { status: 404, statusText: 'Not Found' },
      );

      expect(text()).toContain("This branch's Device was not found.");
      expect(query('app-activation-key-display')).toBeNull();
      // Not disguised as a generic failure, and vice versa.
      expect(text()).not.toContain('The Activation Key could not be regenerated.');
    });

    it('leaves a 401 to the global session-expiry handling', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // The component adds no 401 handling of its own: sessionExpiryInterceptor has already acted
      // (T-25). Here, uninstalled, the error settles into the generic state — never into a key, and
      // never into a not-found that would hide the expired session.
      flushRegeneration(
        { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
        { status: 401, statusText: 'Unauthorized' },
      );

      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain("This branch's Device was not found.");
      expect(text()).toContain('The Activation Key could not be regenerated.');
    });

    it('clears a previous error when the confirmation is reopened', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      flushRegeneration({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });
      expect(text()).toContain('The Activation Key could not be regenerated.');

      query('.branch__regenerate')?.click();
      fixture.detectChanges();

      expect(text()).not.toContain('The Activation Key could not be regenerated.');
      query('.branch__confirm-cancel')?.click();
      fixture.detectChanges();
    });

    it('models no key hash, DeviceRecordId, or shared secret from the regeneration response', () => {
      openConfirmation();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // A response carrying members the Backend's DTO does not have: none reaches the view, because
      // only `activationKey` is ever read from it (FS-02 §1.3, §11).
      flushRegeneration({
        success: true,
        data: {
          activationKey: PLACEHOLDER_REGENERATED_KEY,
          activationKeyHash: 'PLACEHOLDER-ACTIVATION-KEY-HASH',
          activationKeyStatus: 'Unconsumed',
          previousActivationKey: 'PLACEHOLDER-PREVIOUS-KEY',
          deviceRecordId: 'PLACEHOLDER-DEVICE-RECORD-ID',
          protectedSharedSecret: 'PLACEHOLDER-PROTECTED-SECRET',
          sharedSecret: 'PLACEHOLDER-SHARED-SECRET',
        },
      });

      const rendered = text();
      expect(rendered).toContain(PLACEHOLDER_REGENERATED_KEY);
      for (const forbidden of [
        'PLACEHOLDER-ACTIVATION-KEY-HASH',
        'PLACEHOLDER-PREVIOUS-KEY',
        'PLACEHOLDER-DEVICE-RECORD-ID',
        'PLACEHOLDER-PROTECTED-SECRET',
        'PLACEHOLDER-SHARED-SECRET',
      ]) {
        expect(rendered).not.toContain(forbidden);
      }
    });

    it('offers no regeneration action when the branch was not found', () => {
      load(
        { success: false, message: 'Branch not found.', errorCode: 'NOT_FOUND' },
        { status: 404, statusText: 'Not Found' },
      );

      // Nothing to regenerate a key for.
      expect(query('.branch__regenerate')).toBeNull();
    });
  });

  it('shows a not-found view for an unknown branch', () => {
    load(
      { success: false, message: 'Branch not found.', errorCode: 'NOT_FOUND' },
      { status: 404, statusText: 'Not Found' },
    );

    expect(text()).toContain('That branch was not found.');
    expect(element().querySelector('.branch__status--error')).toBeNull();
  });

  it('shows a generic failure state when the Backend fails', () => {
    load(
      { success: false, message: 'Database connection to sql-prod-01 refused.' },
      { status: 500, statusText: 'Error' },
    );

    expect(text()).toContain('The branch could not be loaded.');
    // A genuine failure is not disguised as a not-found, and no Backend detail leaks (FS-02 §11).
    expect(text()).not.toContain('That branch was not found.');
    expect(text()).not.toContain('sql-prod-01');
  });

  it('treats a missing route parameter as not-found without calling the Backend', async () => {
    await createWithRouteParam(null);

    fixture.detectChanges();

    // verify() asserts no request to `/branches/` was ever issued.
    expect(text()).toContain('That branch was not found.');
  });

  it('renders no secrets, keys, or internal identifiers', () => {
    load({
      success: true,
      data: {
        ...placeholderBranch({
          device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
        }),
        activationKey: 'PLACEHOLDER-ACTIVATION-KEY',
        activationKeyHash: 'PLACEHOLDER-ACTIVATION-KEY-HASH',
        deviceRecordId: 'PLACEHOLDER-DEVICE-RECORD-ID',
        protectedSharedSecret: 'PLACEHOLDER-PROTECTED-SECRET',
        sharedSecret: 'PLACEHOLDER-SHARED-SECRET',
      },
    });

    const rendered = text();
    for (const forbidden of [
      'PLACEHOLDER-ACTIVATION-KEY',
      'PLACEHOLDER-ACTIVATION-KEY-HASH',
      'PLACEHOLDER-DEVICE-RECORD-ID',
      'PLACEHOLDER-PROTECTED-SECRET',
      'PLACEHOLDER-SHARED-SECRET',
    ]) {
      expect(rendered).not.toContain(forbidden);
    }
  });
});
