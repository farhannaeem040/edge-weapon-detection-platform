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
