import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { BranchService } from './branch.service';
import { Branch, CreateBranchRequest, CreatedBranch } from './branch.models';

// Every value below is synthetic placeholder data. No real branch, address, contact detail, camera
// URL, or identifier from any real deployment appears in this suite.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const PLACEHOLDER_BRANCH_ID = '11111111-1111-1111-1111-111111111111';

/**
 * A synthetic stand-in for an Activation Key, in the Backend's two-part `keyId.secret` shape
 * (FS-02 §1.4). It is not, and must never be, a real key from any deployment.
 */
const PLACEHOLDER_ACTIVATION_KEY = 'placeholderkeyid.placeholdersecretvalue';

/** A branch-creation request exactly as the Backend's `CreateBranchRequestDto` accepts one. */
function placeholderCreateRequest(): CreateBranchRequest {
  return {
    name: 'Placeholder Branch',
    address: '1 Example Street, Placeholder City',
    contactDetails: 'placeholder@example.invalid',
    cameras: [{ name: 'Front Entrance', rtspUrl: 'rtsp://camera.example.invalid:554/stream1' }],
  };
}

/** A create response exactly as the Backend's `BranchResponseDto.ForCreate` serializes one. */
function placeholderCreatedBranch(): CreatedBranch {
  return { ...placeholderBranch(), activationKey: PLACEHOLDER_ACTIVATION_KEY };
}

/** A branch exactly as the Backend's `BranchResponseDto.ForRead` serializes one. */
function placeholderBranch(overrides: Partial<Branch> = {}): Branch {
  return {
    branchId: PLACEHOLDER_BRANCH_ID,
    name: 'Placeholder Branch',
    address: '1 Example Street, Placeholder City',
    contactDetails: 'placeholder@example.invalid',
    cameras: [
      {
        cameraId: '22222222-2222-2222-2222-222222222222',
        name: 'Front Entrance',
        // Already redacted by the Backend's RtspUrlSanitizer — this is the shape that reaches us.
        rtspUrl: 'rtsp://***@camera.example.invalid:554/stream1',
        enabled: true,
      },
    ],
    device: { activationStatus: 'Unactivated' },
    ...overrides,
  };
}

describe('BranchService', () => {
  let service: BranchService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(BranchService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  describe('list', () => {
    it('calls GET on the exact branch list endpoint', () => {
      service.list().subscribe();

      const request = httpTesting.expectOne(BRANCHES_URL);
      expect(request.request.method).toBe('GET');
      request.flush({ success: true, data: [] });
    });

    it('maps the standard success envelope to the branches it carries', () => {
      let branches: Branch[] | undefined;
      service.list().subscribe((result) => (branches = result));

      const branch = placeholderBranch();
      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, message: null, data: [branch] });

      expect(branches).toEqual([branch]);
    });

    it('maps an empty success envelope to an empty list', () => {
      let branches: Branch[] | undefined;
      service.list().subscribe((result) => (branches = result));

      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [] });

      // No branches is a valid result, not a failure.
      expect(branches).toEqual([]);
    });

    it('errors when a 200 carries an unsuccessful envelope', () => {
      let errored = false;
      service.list().subscribe({ error: () => (errored = true) });

      httpTesting.expectOne(BRANCHES_URL).flush({ success: false, message: 'Failure.', data: null });

      // A contract violation, not an empty result — it must not be rendered as "no branches".
      expect(errored).toBeTrue();
    });

    it('propagates a Backend failure', () => {
      let errored = false;
      service.list().subscribe({ error: () => (errored = true) });

      httpTesting
        .expectOne(BRANCHES_URL)
        .flush({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });

      expect(errored).toBeTrue();
    });
  });

  describe('get', () => {
    it('calls GET on the exact branch detail endpoint', () => {
      service.get(PLACEHOLDER_BRANCH_ID).subscribe();

      const request = httpTesting.expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`);
      expect(request.request.method).toBe('GET');
      request.flush({ success: true, data: placeholderBranch() });
    });

    it('maps the standard success envelope to the branch it carries', () => {
      let branch: Branch | null | undefined;
      service.get(PLACEHOLDER_BRANCH_ID).subscribe((result) => (branch = result));

      const expected = placeholderBranch();
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: true, message: null, data: expected });

      expect(branch).toEqual(expected);
    });

    it('emits null when the Backend answers 404', () => {
      let branch: Branch | null | undefined = undefined;
      let errored = false;
      service.get(PLACEHOLDER_BRANCH_ID).subscribe({
        next: (result) => (branch = result),
        error: () => (errored = true),
      });

      // The endpoint's documented not-found outcome (FS-02 §10.3) — an outcome, not a fault.
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush(
          { success: false, message: 'Branch not found.', errorCode: 'NOT_FOUND' },
          { status: 404, statusText: 'Not Found' },
        );

      expect(branch).toBeNull();
      expect(errored).toBeFalse();
    });

    it('propagates a failure that is not a 404', () => {
      let errored = false;
      service.get(PLACEHOLDER_BRANCH_ID).subscribe({ error: () => (errored = true) });

      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });

      expect(errored).toBeTrue();
    });

    it('does not swallow a 401 into a not-found result', () => {
      let branch: Branch | null | undefined = undefined;
      let errored = false;
      service.get(PLACEHOLDER_BRANCH_ID).subscribe({
        next: (result) => (branch = result),
        error: () => (errored = true),
      });

      // A 401 is the session-expiry interceptor's business (T-25); turning it into "no such branch"
      // would hide an expired session behind a not-found view.
      httpTesting
        .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
        .flush(
          { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
          { status: 401, statusText: 'Unauthorized' },
        );

      expect(errored).toBeTrue();
      expect(branch).toBeUndefined();
    });
  });

  describe('create', () => {
    it('calls POST on the exact branch creation endpoint', () => {
      service.create(placeholderCreateRequest()).subscribe();

      const request = httpTesting.expectOne(BRANCHES_URL);
      expect(request.request.method).toBe('POST');
      request.flush({ success: true, data: placeholderCreatedBranch() });
    });

    it('sends exactly the Backend CreateBranchRequestDto fields and nothing else', () => {
      service.create(placeholderCreateRequest()).subscribe();

      const request = httpTesting.expectOne(BRANCHES_URL);
      const body = request.request.body as CreateBranchRequest;

      // Field-for-field against CreateBranchRequestDto/CameraConfigDto. The key assertions are the
      // exact key sets: an extra member here (a client-generated id, an activation field, an
      // `enabled` flag) would be a contract the Backend never agreed to.
      expect(Object.keys(body).sort()).toEqual(['address', 'cameras', 'contactDetails', 'name']);
      expect(Object.keys(body.cameras[0]).sort()).toEqual(['name', 'rtspUrl']);
      expect(body).toEqual(placeholderCreateRequest());

      request.flush({ success: true, data: placeholderCreatedBranch() });
    });

    it('maps the standard success envelope to the created branch and its Activation Key', () => {
      let created: CreatedBranch | undefined;
      service.create(placeholderCreateRequest()).subscribe((result) => (created = result));

      const expected = placeholderCreatedBranch();
      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, message: null, data: expected });

      expect(created).toEqual(expected);
      expect(created?.activationKey).toBe(PLACEHOLDER_ACTIVATION_KEY);
      // The reserved Device is unactivated and carries no deviceId at creation (FS-02 §10.1).
      expect(created?.device.activationStatus).toBe('Unactivated');
      expect(created?.device.deviceId).toBeUndefined();
    });

    it('errors when a 201 envelope carries no Activation Key', () => {
      let errored = false;
      service.create(placeholderCreateRequest()).subscribe({ error: () => (errored = true) });

      const { activationKey: _omitted, ...withoutKey } = placeholderCreatedBranch();
      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: withoutKey });

      // The key cannot be re-fetched, so a create response without one is a contract violation that
      // must not be reported as success.
      expect(errored).toBeTrue();
    });

    it('propagates a Backend validation failure', () => {
      let errored = false;
      service.create(placeholderCreateRequest()).subscribe({ error: () => (errored = true) });

      httpTesting
        .expectOne(BRANCHES_URL)
        .flush(
          { success: false, message: 'The request is invalid.', errorCode: 'VALIDATION_ERROR' },
          { status: 400, statusText: 'Bad Request' },
        );

      expect(errored).toBeTrue();
    });

    it('does not store the Activation Key on the service or in browser storage', () => {
      service.create(placeholderCreateRequest()).subscribe();
      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: placeholderCreatedBranch() });

      // The key belongs to the caller's response value alone: the service keeps no copy anywhere it
      // could outlive that (FS-02 §5.4, §7). It holds only its collaborator and its URL — there is
      // no field for a key to have been stashed in.
      expect(Object.keys(service).sort()).toEqual(['branchesUrl', 'http']);
      expect(Object.values(service)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(sessionStorage.getItem('activationKey')).toBeNull();
      expect(JSON.stringify(sessionStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(JSON.stringify(localStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    });
  });

  it('never requests an activation-key, secret, or internal-id route', () => {
    service.list().subscribe();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [] });

    service.get(PLACEHOLDER_BRANCH_ID).subscribe();
    httpTesting
      .expectOne(`${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`)
      .flush({ success: true, data: placeholderBranch() });

    // verify() in afterEach asserts these two were the only requests the read views ever issue.
  });
});
