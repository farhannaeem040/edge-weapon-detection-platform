import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { BranchService } from './branch.service';
import { Branch } from './branch.models';

// Every value below is synthetic placeholder data. No real branch, address, contact detail, camera
// URL, or identifier from any real deployment appears in this suite.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const PLACEHOLDER_BRANCH_ID = '11111111-1111-1111-1111-111111111111';

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
