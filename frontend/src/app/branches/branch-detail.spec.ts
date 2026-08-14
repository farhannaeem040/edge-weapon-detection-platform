import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';

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
        cameraKey: 'cam-key-14',
        name: 'Front Entrance',
        rtspUrl: REDACTED_RTSP_URL,
        enabled: true,
        sourceOrder: 0,
        outputPath: 'cameras/00000000-0000-0000-0000-000000000000',
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
  async function createWithRouteParam(
    branchId: string | null,
    queryParams: Record<string, string> = {},
  ): Promise<void> {
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
              // FS-14 §5, IP-16 T-12: branch-detail.ts reads the `tab` query param to preselect
              // the Live Monitoring tab on a deep link — empty by default, so every existing test
              // keeps its default 'overview' tab behaviour.
              queryParamMap: convertToParamMap(queryParams),
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

  describe('camera stream URL (manual-review round 2, Correction 1)', () => {
    it('labels the camera name and stream URL explicitly', () => {
      load({ success: true, data: placeholderBranch() });

      expect(text()).toContain('Camera name:');
      // FS-11 §11 relabel: a camera now has two URLs, so the input one is named explicitly.
      expect(text()).toContain('Input stream URL:');
      expect(text()).toContain(REDACTED_RTSP_URL);
    });

    it('never renders the stream URL as a clickable/navigable link', () => {
      load({ success: true, data: placeholderBranch() });

      expect(element().querySelector('.branch__camera-url a')).toBeNull();
    });

    it('labels the camera status explicitly', () => {
      load({ success: true, data: placeholderBranch() });

      expect(text()).toContain('Status:');
      expect(text()).toContain('Enabled');
    });

    it('offers an independent copy action for each camera, never automatic clipboard writes', () => {
      load({
        success: true,
        data: placeholderBranch({
          cameras: [
            {
              cameraId: '22222222-2222-2222-2222-222222222222',
              cameraKey: 'cam-key-15',
              name: 'Camera One',
              rtspUrl: 'rtsp://my-server-ip:8554/camera1',
              enabled: true,
              sourceOrder: 1,
              outputPath: 'cameras/00000000-0000-0000-0000-000000000001',
            },
            {
              cameraId: '55555555-5555-5555-5555-555555555555',
              cameraKey: 'cam-key-16',
              name: 'Camera Two',
              rtspUrl: 'rtsp://my-server-ip:8554/camera2',
              enabled: true,
              sourceOrder: 2,
              outputPath: 'cameras/00000000-0000-0000-0000-000000000002',
            },
          ],
        }),
      });

      const copyButtons = element().querySelectorAll(
        '.branch__camera-copy',
      ) as NodeListOf<HTMLButtonElement>;
      expect(copyButtons.length).toBe(2);
      expect(copyButtons[0].tagName).toBe('BUTTON');
      expect(copyButtons[1].tagName).toBe('BUTTON');
    });

    it('wraps long URLs safely rather than overflowing the card', () => {
      load({ success: true, data: placeholderBranch() });

      const urlElement = element().querySelector('.branch__camera-url') as HTMLElement | null;
      const styles = urlElement ? getComputedStyle(urlElement) : null;
      expect(styles?.overflowWrap).toBe('anywhere');
    });
  });

  describe('edit action (T-45)', () => {
    it('renders an edit action pointing at this branch edit route', () => {
      load({ success: true, data: placeholderBranch() });

      const editLink = element().querySelector('.branch__edit') as HTMLAnchorElement;
      expect(editLink).not.toBeNull();
      expect(editLink.getAttribute('href')).toBe(`/branches/${PLACEHOLDER_BRANCH_ID}/edit`);
    });

    it('names the branch in the edit action aria-label and title', () => {
      load({ success: true, data: placeholderBranch() });

      const editLink = element().querySelector('.branch__edit') as HTMLAnchorElement;
      expect(editLink.getAttribute('aria-label')).toBe('Edit branch Alpha Branch');
      expect(editLink.getAttribute('title')).toBe('Edit branch Alpha Branch');
    });

    it('does not render the edit action for a branch that was not found', async () => {
      load({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });

      expect(element().querySelector('.branch__edit')).toBeNull();
    });
  });

  describe('delete action (T-46)', () => {
    const DELETE_URL = `${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`;

    it('renders a delete action naming the branch, as a button not a link', () => {
      load({ success: true, data: placeholderBranch() });

      const del = element().querySelector('.branch__delete') as HTMLButtonElement;
      expect(del).not.toBeNull();
      expect(del.tagName).toBe('BUTTON');
      expect(del.getAttribute('aria-label')).toBe('Delete branch Alpha Branch');
      expect(del.getAttribute('title')).toBe('Delete branch Alpha Branch');
    });

    it('opens a confirmation and issues no request on the first click', () => {
      load({ success: true, data: placeholderBranch() });

      (element().querySelector('.branch__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      expect(element().querySelector('app-branch-delete-confirm')).not.toBeNull();
    });

    it('sends no request when the confirmation is cancelled', () => {
      load({ success: true, data: placeholderBranch() });
      (element().querySelector('.branch__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      (element().querySelector('.delete-confirm__cancel') as HTMLButtonElement).click();
      fixture.detectChanges();

      expect(element().querySelector('app-branch-delete-confirm')).toBeNull();
    });

    it('calls the exact endpoint once on confirm and navigates to the branch list', () => {
      const navigate = spyOn(TestBed.inject(Router), 'navigateByUrl');
      load({ success: true, data: placeholderBranch() });
      (element().querySelector('.branch__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      const requests = httpTesting.match(DELETE_URL);
      expect(requests.length).toBe(1);
      expect(requests[0].request.method).toBe('DELETE');
      requests[0].flush({ success: true, message: 'Branch deleted.' });
      fixture.detectChanges();

      expect(navigate).toHaveBeenCalledOnceWith('/branches');
    });

    it('navigates to the list on a 404 (already gone)', () => {
      const navigate = spyOn(TestBed.inject(Router), 'navigateByUrl');
      load({ success: true, data: placeholderBranch() });
      (element().querySelector('.branch__delete') as HTMLButtonElement).click();
      fixture.detectChanges();
      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      httpTesting
        .expectOne(DELETE_URL)
        .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
      fixture.detectChanges();

      expect(navigate).toHaveBeenCalledOnceWith('/branches');
    });

    it('shows a generic failure and does not navigate on a server error', () => {
      const navigate = spyOn(TestBed.inject(Router), 'navigateByUrl');
      load({ success: true, data: placeholderBranch() });
      (element().querySelector('.branch__delete') as HTMLButtonElement).click();
      fixture.detectChanges();
      (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
      fixture.detectChanges();

      httpTesting
        .expectOne(DELETE_URL)
        .flush({ success: false, message: 'Boom.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      expect(navigate).not.toHaveBeenCalled();
      expect(text()).toContain('The branch could not be deleted.');
      expect(text()).not.toContain('Boom.');
    });
  });

  it('renders every configured camera', () => {
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: '22222222-2222-2222-2222-222222222222',
            cameraKey: 'cam-key-17',
            name: 'Front Entrance',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
            sourceOrder: 3,
            outputPath: 'cameras/00000000-0000-0000-0000-000000000003',
          },
          {
            cameraId: '55555555-5555-5555-5555-555555555555',
            cameraKey: 'cam-key-18',
            name: 'Rear Exit',
            rtspUrl: 'rtsp://camera.example.invalid:554/stream2',
            enabled: false,
            sourceOrder: 4,
            outputPath: 'cameras/00000000-0000-0000-0000-000000000004',
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
            cameraKey: 'cam-key-19',
            name: 'Rear Exit',
            rtspUrl: 'rtsp://camera.example.invalid:554/stream2',
            enabled: false,
            sourceOrder: 5,
            outputPath: 'cameras/00000000-0000-0000-0000-000000000005',
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

    // The Backend already redacted the credential span; the view displays exactly what arrived,
    // alongside the "Camera stream URL:" label — never re-redacted or altered.
    const normalized = (element().querySelector('.branch__camera-url')?.textContent ?? '')
      .replace(/\s+/g, ' ')
      .trim();
    expect(normalized).toBe(`Input stream URL: ${REDACTED_RTSP_URL}`);
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

    /**
     * Answers a successful regeneration and the branch re-read the component issues on success (P1:
     * the status badge must reflect the new state — `ReactivationRequired` for a Device that had been
     * Activated). `branchAfter` is what that refresh returns; it defaults to the Unactivated
     * placeholder, matching the default confirmation, whose Device stays Unactivated on regeneration.
     */
    function succeed(branchAfter: Branch = placeholderBranch()): void {
      flushRegeneration({ success: true, data: { activationKey: PLACEHOLDER_REGENERATED_KEY } });
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: branchAfter });
      fixture.detectChanges();
    }

    /** The branch as it stands after regenerating an Activated Device: revoked, ReactivationRequired. */
    function reactivationRequiredBranch(): Branch {
      return placeholderBranch({
        device: { activationStatus: 'ReactivationRequired', deviceId: PLACEHOLDER_DEVICE_ID },
      });
    }

    /** The branch as loaded for an Activated Device (the destructive-regeneration starting point). */
    function activatedBranch(): Branch {
      return placeholderBranch({
        device: { activationStatus: 'Activated', deviceId: PLACEHOLDER_DEVICE_ID },
      });
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

    it('shows a benign generation prompt for an unactivated Device, with no destructive warning', () => {
      // Default branch is Unactivated: there is no live credential and no running Jetson, so this is
      // the ordinary first-activation key-generation flow (IP-05 P1, FS-02 §5.3).
      openConfirmation();

      const rendered = text();
      expect(rendered).toContain('has not been activated');
      expect(rendered).toContain('stops working immediately');
      expect(rendered).toContain('Device ID is unaffected');
      expect(rendered).toContain('shown to you once');
      // None of the destructive credential-revocation language, and no Jetson-lock warning.
      expect(rendered).not.toContain('revoked');
      expect(rendered).not.toContain('lock');
      expect(rendered).not.toContain('destructive');
      expect(query('.branch__confirm--destructive')).toBeNull();
    });

    it('shows a destructive credential-revocation warning for an activated Device', () => {
      openConfirmation(activatedBranch());

      const rendered = text();
      expect(rendered).toContain('destructive');
      expect(rendered).toContain('revoked immediately');
      expect(rendered).toContain('Jetson will lock');
      expect(rendered).toContain('provisioned on the Jetson manually');
      expect(rendered).toContain('Device ID is preserved');
      expect(rendered).toContain('shown to you once');
      expect(query('.branch__confirm--destructive')).not.toBeNull();
    });

    it('shows the destructive warning for a ReactivationRequired Device too', () => {
      // Regenerating again while already ReactivationRequired is still a credential reset (§4.1), so
      // the destructive warning applies exactly as for an Activated Device.
      openConfirmation(reactivationRequiredBranch());

      const rendered = text();
      expect(rendered).toContain('revoked immediately');
      expect(rendered).toContain('Jetson will lock');
      expect(query('.branch__confirm--destructive')).not.toBeNull();
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

      // Exactly one regeneration POST; the success then triggers a single branch re-read (P1), which
      // is a GET to a different URL — answered here so no request is left pending for verify().
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: placeholderBranch() });
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

      // The success re-reads the branch (P1); answer it so nothing is left pending.
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: placeholderBranch() });
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

    it('re-reads the branch on success and keeps the disclosure on screen', () => {
      // P1: a successful regeneration refreshes the branch/device state so the badge reflects the new
      // status. The re-read happens behind the disclosure — the Admin stays on the branch with the key
      // on screen, and there is no navigation. (The key itself is never re-fetched; the refresh is an
      // ordinary branch read that returns no key.)
      openConfirmation(activatedBranch());
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed(reactivationRequiredBranch());

      expect(query('app-activation-key-display')).not.toBeNull();
      expect(query('.activation-key__value')?.textContent?.trim()).toBe(PLACEHOLDER_REGENERATED_KEY);
      expect(text()).toContain('Alpha Branch');
    });

    it('shows "Reactivation required" after regenerating an activated Device', () => {
      openConfirmation(activatedBranch());
      expect(element().querySelector('.device-status__label')?.textContent?.trim()).toBe('Activated');

      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed(reactivationRequiredBranch());

      // The badge, refreshed behind the disclosure, now reads the revocation state — never "Offline".
      expect(element().querySelector('.device-status__label')?.textContent?.trim()).toBe(
        'Reactivation required',
      );
      expect(text()).not.toContain('Offline');

      // Dismissing the disclosure leaves the refreshed status in place.
      query('.activation-key__continue')?.click();
      fixture.detectChanges();
      expect(element().querySelector('.device-status__label')?.textContent?.trim()).toBe(
        'Reactivation required',
      );
    });

    it('does not optimistically change the device status before the response', () => {
      openConfirmation(activatedBranch());
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // In flight: the badge still reads the loaded status. The new state comes only from the
      // Backend-confirmed re-read, never from a client guess.
      expect(element().querySelector('.device-status__label')?.textContent?.trim()).toBe('Activated');
      expect(query('app-activation-key-display')).toBeNull();

      succeed(reactivationRequiredBranch());
    });

    it('shows safe retry guidance and no key on a 409 regeneration conflict', () => {
      openConfirmation(activatedBranch());
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      // A lost concurrent-regeneration race (IP-05 §3): 409 with the conflict errorCode.
      flushRegeneration(
        {
          success: false,
          message: 'A concurrent regeneration won the race.',
          errorCode: 'ACTIVATION_KEY_REGENERATION_CONFLICT',
        },
        { status: 409, statusText: 'Conflict' },
      );
      // The component re-reads the branch to reflect whatever the winning request left.
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: reactivationRequiredBranch() });
      fixture.detectChanges();

      expect(text()).toContain('no key was issued to you');
      // No key is shown — the losing request received none.
      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      // Not disguised as a generic failure or a not-found.
      expect(text()).not.toContain('The Activation Key could not be regenerated.');
      expect(text()).not.toContain("This branch's Device was not found.");
    });

    it('clears a previously shown key when a later attempt hits a 409 conflict', () => {
      openConfirmation(activatedBranch());
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();
      succeed(reactivationRequiredBranch());
      expect(text()).toContain(PLACEHOLDER_REGENERATED_KEY);

      // Complete, reopen, and try again — this time the Backend reports a conflict.
      query('.activation-key__continue')?.click();
      fixture.detectChanges();
      query('.branch__regenerate')?.click();
      fixture.detectChanges();
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      flushRegeneration(
        { success: false, errorCode: 'ACTIVATION_KEY_REGENERATION_CONFLICT' },
        { status: 409, statusText: 'Conflict' },
      );
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: reactivationRequiredBranch() });
      fixture.detectChanges();

      // The stale key from the earlier success is gone.
      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_REGENERATED_KEY);
      expect(text()).toContain('no key was issued to you');
    });

    it('does not automatically retry after a 409 conflict', () => {
      openConfirmation(activatedBranch());
      query('.branch__confirm-regenerate')?.click();
      fixture.detectChanges();

      flushRegeneration(
        { success: false, errorCode: 'ACTIVATION_KEY_REGENERATION_CONFLICT' },
        { status: 409, statusText: 'Conflict' },
      );
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: activatedBranch() });
      fixture.detectChanges();

      // No second regeneration request is issued — recovery is the Admin's explicit action, never an
      // automatic re-send. expectNone throws if one exists; the boolean records that it did not.
      httpTesting.expectNone(REGENERATE_URL);
      expect(text()).toContain('no key was issued to you');
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
      // The success re-reads the branch (P1); answer it so nothing is left pending.
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, data: placeholderBranch() });
      fixture.detectChanges();

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

  // --- FS-11 §11: annotated per-camera output discovery -----------------------------------------

  it('shows the annotated output URL separately from the input stream URL', () => {
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: '22222222-2222-2222-2222-222222222222',
            cameraKey: 'cam-key-20',
            name: 'Front Camera',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
            sourceOrder: 0,
            outputPath: 'cameras/22222222-2222-2222-2222-222222222222',
            outputStreamUrl:
              'rtsp://100.98.226.80:8554/cameras/22222222-2222-2222-2222-222222222222',
          },
        ],
        device: {
          activationStatus: 'Activated',
          annotatedOutputBaseUrl: 'rtsp://100.98.226.80:8554',
        },
      }),
    });

    // Both URLs are present, each under its own explicit label — they must never be confused.
    expect(text()).toContain('Input stream URL:');
    expect(text()).toContain(REDACTED_RTSP_URL);
    expect(text()).toContain('Annotated output URL:');
    expect(text()).toContain(
      'rtsp://100.98.226.80:8554/cameras/22222222-2222-2222-2222-222222222222',
    );
    // The legacy shared mount must never be assumed.
    expect(text()).not.toContain('ds-test');
  });

  it('renders one annotated output entry per camera', () => {
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            cameraKey: 'cam-key-21',
            name: 'Front Camera',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
            sourceOrder: 0,
            outputPath: 'cameras/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            outputStreamUrl: 'rtsp://host.example.invalid:8554/cameras/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          },
          {
            cameraId: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            cameraKey: 'cam-key-22',
            name: 'Rear Entrance',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
            sourceOrder: 1,
            outputPath: 'cameras/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            outputStreamUrl: 'rtsp://host.example.invalid:8554/cameras/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
          },
        ],
        device: {
          activationStatus: 'Activated',
          annotatedOutputBaseUrl: 'rtsp://host.example.invalid:8554',
        },
      }),
    });

    const outputs = Array.from(element().querySelectorAll('.branch__camera-output'));
    expect(outputs.length).toBe(2);
    expect(text()).toContain('cameras/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
    expect(text()).toContain('cameras/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');
  });

  it('shows a configuration message instead of a URL when no output base is configured', () => {
    load({ success: true, data: placeholderBranch() }); // fixture omits outputStreamUrl

    expect(text()).toContain('Output base URL not configured');
    // Nothing may be fabricated from the request host.
    expect(text()).not.toContain('rtsp://localhost');
    expect(element().querySelector('.branch__camera-copy-output')).toBeNull();
  });

  it('copies the annotated output URL, not the input URL', () => {
    const outputUrl = 'rtsp://100.98.226.80:8554/cameras/22222222-2222-2222-2222-222222222222';
    load({
      success: true,
      data: placeholderBranch({
        cameras: [
          {
            cameraId: '22222222-2222-2222-2222-222222222222',
            cameraKey: 'cam-key-23',
            name: 'Front Camera',
            rtspUrl: REDACTED_RTSP_URL,
            enabled: true,
            sourceOrder: 0,
            outputPath: 'cameras/22222222-2222-2222-2222-222222222222',
            outputStreamUrl: outputUrl,
          },
        ],
        device: {
          activationStatus: 'Activated',
          annotatedOutputBaseUrl: 'rtsp://100.98.226.80:8554',
        },
      }),
    });

    const written: string[] = [];
    spyOn(navigator.clipboard, 'writeText').and.callFake((value: string) => {
      written.push(value);
      return Promise.resolve();
    });

    const copyOutput = element().querySelector<HTMLButtonElement>('.branch__camera-copy-output');
    copyOutput?.click();

    expect(written).toEqual([outputUrl]);
  });

  // --- FS-14 §5, IP-16 T-12: Overview | Live Monitoring tabs ---
  describe('Live Monitoring tab', () => {
    const CAMERAS_URL = `${environment.apiBaseUrl}/branches/${PLACEHOLDER_BRANCH_ID}/live-monitoring/cameras`;

    it('defaults to the Overview tab, showing no live-monitoring request', () => {
      load({ success: true, data: placeholderBranch() });

      httpTesting.expectNone(CAMERAS_URL);
      expect(element().querySelector('.branch__camera-list')).not.toBeNull();
      expect(element().querySelector('app-live-monitoring')).toBeNull();
    });

    it('switches to Live Monitoring on click, hosting the live-monitoring component', () => {
      load({ success: true, data: placeholderBranch() });

      const monitoringTab = Array.from(element().querySelectorAll('button[role="tab"]')).find((b) =>
        b.textContent?.includes('Live Monitoring'),
      ) as HTMLButtonElement;
      monitoringTab.click();
      fixture.detectChanges();

      expect(element().querySelector('app-live-monitoring')).not.toBeNull();
      expect(element().querySelector('.branch__camera-list')).toBeNull();

      // The hosted component issues its own request; flush it so httpTesting.verify() is satisfied.
      httpTesting.expectOne(CAMERAS_URL).flush({ success: true, data: [] });
    });

    it('returns to Overview on click, without re-requesting the branch', () => {
      load({ success: true, data: placeholderBranch() });

      const tabs = () => Array.from(element().querySelectorAll('button[role="tab"]'));
      (tabs().find((b) => b.textContent?.includes('Live Monitoring')) as HTMLButtonElement).click();
      fixture.detectChanges();
      httpTesting.expectOne(CAMERAS_URL).flush({ success: true, data: [] });

      (tabs().find((b) => b.textContent?.includes('Overview')) as HTMLButtonElement).click();
      fixture.detectChanges();

      expect(element().querySelector('.branch__camera-list')).not.toBeNull();
      expect(element().querySelector('app-live-monitoring')).toBeNull();
      httpTesting.expectNone(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`);
    });

    it('preselects the Live Monitoring tab when the route already carries tab=monitoring', async () => {
      await createWithRouteParam(PLACEHOLDER_BRANCH_ID, { tab: 'monitoring' });
      load({ success: true, data: placeholderBranch() });

      expect(element().querySelector('app-live-monitoring')).not.toBeNull();
      httpTesting.expectOne(CAMERAS_URL).flush({ success: true, data: [] });
    });
  });
});
