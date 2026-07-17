import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormArray, FormGroup } from '@angular/forms';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { authGuard } from '../core/auth.guard';
import { routes } from '../app.routes';
import { BranchEditComponent } from './branch-edit';
import { Branch, UpdateBranchRequest } from './branch.models';

// Every value here is synthetic: `.invalid` RTSP hosts and placeholder GUIDs. No real key,
// credential, camera, or deployment value appears.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const PLACEHOLDER_BRANCH_ID = '11111111-1111-1111-1111-111111111111';
const BRANCH_URL = `${BRANCHES_URL}/${PLACEHOLDER_BRANCH_ID}`;
const FIRST_CAMERA_ID = '22222222-2222-2222-2222-222222222222';
const SECOND_CAMERA_ID = '33333333-3333-3333-3333-333333333333';
const FIRST_RTSP_URL = 'rtsp://camera.example.invalid:554/stream1';
const SECOND_RTSP_URL = 'rtsp://camera.example.invalid:554/stream2';
const NEW_RTSP_URL = 'rtsp://camera.example.invalid:554/stream3';

function placeholderBranch(overrides: Partial<Branch> = {}): Branch {
  return {
    branchId: PLACEHOLDER_BRANCH_ID,
    name: 'Alpha Branch',
    address: '1 Example Street, Placeholder City',
    contactDetails: 'placeholder@example.invalid',
    cameras: [
      { cameraId: FIRST_CAMERA_ID, name: 'Front Entrance', rtspUrl: FIRST_RTSP_URL, enabled: true },
      { cameraId: SECOND_CAMERA_ID, name: 'Loading Bay', rtspUrl: SECOND_RTSP_URL, enabled: true },
    ],
    device: { activationStatus: 'Unactivated' },
    ...overrides,
  };
}

describe('BranchEditComponent', () => {
  let fixture: ComponentFixture<BranchEditComponent>;
  let component: BranchEditComponent;
  let httpTesting: HttpTestingController;
  let router: Router;

  async function createWithRouteParam(branchId: string | null): Promise<void> {
    TestBed.resetTestingModule();

    await TestBed.configureTestingModule({
      imports: [BranchEditComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { paramMap: convertToParamMap(branchId === null ? {} : { branchId }) },
          },
        },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(BranchEditComponent);
    component = fixture.componentInstance;
  }

  beforeEach(() => createWithRouteParam(PLACEHOLDER_BRANCH_ID));

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  function query(selector: string): HTMLElement | null {
    return element().querySelector(selector) as HTMLElement | null;
  }

  function form(): FormGroup {
    return component['form'] as unknown as FormGroup;
  }

  function cameras(): FormArray {
    return form().get('cameras') as FormArray;
  }

  /** Renders the component and answers the initial load with the given branch. */
  function load(branch: Branch = placeholderBranch()): void {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCH_URL).flush({ success: true, data: branch });
    fixture.detectChanges();
  }

  function submitForm(): void {
    (query('.branch-edit__form') as HTMLFormElement).dispatchEvent(
      new Event('submit', { bubbles: true }),
    );
    fixture.detectChanges();
  }

  describe('route protection (T-45)', () => {
    it('guards the edit route with authGuard', () => {
      // The Backend enforces the real boundary; the guard keeps the view from rendering without a
      // local session, exactly as the other branch routes are guarded (FS-01 §10).
      const editRoute = routes.find((r) => r.path === 'branches/:branchId/edit');
      expect(editRoute).toBeDefined();
      expect(editRoute?.canActivate).toContain(authGuard);
    });

    it('is declared before the two-segment detail route so it cannot be mis-matched', () => {
      const editIndex = routes.findIndex((r) => r.path === 'branches/:branchId/edit');
      const detailIndex = routes.findIndex((r) => r.path === 'branches/:branchId');
      expect(editIndex).toBeGreaterThanOrEqual(0);
      expect(editIndex).toBeLessThan(detailIndex);
    });
  });

  describe('initial load', () => {
    it('requests the branch named by the route parameter', () => {
      fixture.detectChanges();

      const request = httpTesting.expectOne(BRANCH_URL);
      expect(request.request.method).toBe('GET');
      request.flush({ success: true, data: placeholderBranch() });
    });

    it('shows a loading state while the request is in flight', () => {
      fixture.detectChanges();

      expect(text()).toContain('Loading branch');

      httpTesting.expectOne(BRANCH_URL).flush({ success: true, data: placeholderBranch() });
    });

    it('shows a not-found state when the branch does not exist, and issues no PUT', () => {
      fixture.detectChanges();
      httpTesting
        .expectOne(BRANCH_URL)
        .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
      fixture.detectChanges();

      expect(text()).toContain('That branch was not found.');
      expect(query('.branch-edit__form')).toBeNull();
    });

    it('shows a generic failure state without surfacing Backend detail', () => {
      fixture.detectChanges();
      httpTesting
        .expectOne(BRANCH_URL)
        .flush(
          { success: false, message: 'sql-prod-01 refused the connection.' },
          { status: 500, statusText: 'Error' },
        );
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be loaded.');
      expect(text()).not.toContain('sql-prod-01');
    });
  });

  describe('form population', () => {
    it('populates the scalar fields from the loaded branch', () => {
      load();

      expect(form().get('name')?.value).toBe('Alpha Branch');
      expect(form().get('address')?.value).toBe('1 Example Street, Placeholder City');
      expect(form().get('contactDetails')?.value).toBe('placeholder@example.invalid');
    });

    it('renders one camera row per existing camera', () => {
      load();

      expect(cameras().length).toBe(2);
      expect(element().querySelectorAll('app-camera-config-form').length).toBe(2);
    });

    it('retains each existing camera public id in a hidden control', () => {
      load();

      expect(cameras().at(0).getRawValue().cameraId).toBe(FIRST_CAMERA_ID);
      expect(cameras().at(1).getRawValue().cameraId).toBe(SECOND_CAMERA_ID);
    });

    it('populates each camera name and RTSP URL', () => {
      load();

      expect(cameras().at(0).getRawValue()).toEqual(
        jasmine.objectContaining({ name: 'Front Entrance', rtspUrl: FIRST_RTSP_URL }),
      );
    });
  });

  describe('camera reconcile', () => {
    it('adds a camera row with no id', () => {
      load();

      query('.branch-edit__add-camera')?.click();
      fixture.detectChanges();

      expect(cameras().length).toBe(3);
      // A new camera must carry no id, so the Backend assigns a fresh identity (FS-03 §5.2).
      expect(cameras().at(2).getRawValue().cameraId).toBeNull();
    });

    it('removes a camera row while more than one remains', () => {
      load();

      element().querySelectorAll<HTMLButtonElement>('.camera__remove')[0].click();
      fixture.detectChanges();

      expect(cameras().length).toBe(1);
      // The surviving row is the one not removed, keeping its id.
      expect(cameras().at(0).getRawValue().cameraId).toBe(SECOND_CAMERA_ID);
    });

    it('offers no remove control for the final remaining camera', () => {
      load(placeholderBranch({ cameras: [placeholderBranch().cameras[0]] }));

      expect(cameras().length).toBe(1);
      expect(query('.camera__remove')).toBeNull();
    });

    it('refuses to remove the final camera even when asked directly', () => {
      load(placeholderBranch({ cameras: [placeholderBranch().cameras[0]] }));

      component['removeCamera'](0);
      fixture.detectChanges();

      expect(cameras().length).toBe(1);
    });
  });

  describe('submission', () => {
    it('puts exactly the Backend contract, keeping ids for edits and omitting them for adds', () => {
      load();

      // Edit the first camera in place, remove the second, add a third.
      cameras().at(0).patchValue({ name: 'Front Door', rtspUrl: FIRST_RTSP_URL });
      element().querySelectorAll<HTMLButtonElement>('.camera__remove')[1].click();
      fixture.detectChanges();
      query('.branch-edit__add-camera')?.click();
      fixture.detectChanges();
      cameras().at(1).patchValue({ name: 'Roof', rtspUrl: NEW_RTSP_URL });
      form().patchValue({ name: '  Alpha Branch Renamed  ' });
      fixture.detectChanges();

      submitForm();

      const request = httpTesting.expectOne(BRANCH_URL);
      expect(request.request.method).toBe('PUT');
      const body = request.request.body as UpdateBranchRequest;
      expect(body.name).toBe('Alpha Branch Renamed');
      expect(body.cameras).toEqual([
        { cameraId: FIRST_CAMERA_ID, name: 'Front Door', rtspUrl: FIRST_RTSP_URL },
        { name: 'Roof', rtspUrl: NEW_RTSP_URL },
      ]);
      // The removed camera is simply absent, and the new one carries no id at all.
      expect(JSON.stringify(body)).not.toContain(SECOND_CAMERA_ID);
      expect(body.cameras[1].cameraId).toBeUndefined();

      request.flush({ success: true, data: placeholderBranch() });
    });

    it('navigates back to the branch detail view on success', () => {
      const navigate = spyOn(router, 'navigateByUrl');
      load();

      submitForm();
      httpTesting.expectOne(BRANCH_URL).flush({ success: true, data: placeholderBranch() });
      fixture.detectChanges();

      expect(navigate).toHaveBeenCalledOnceWith(`/branches/${PLACEHOLDER_BRANCH_ID}`);
    });

    it('prevents a duplicate submission while a request is in flight', () => {
      load();

      submitForm();
      submitForm();
      submitForm();

      const requests = httpTesting.match(BRANCH_URL);
      expect(requests.length).toBe(1);
      requests[0].flush({ success: true, data: placeholderBranch() });
    });

    it('disables the submit button while the request is in flight', () => {
      load();
      submitForm();

      expect((query('.branch-edit__submit') as HTMLButtonElement).disabled).toBeTrue();

      httpTesting.expectOne(BRANCH_URL).flush({ success: true, data: placeholderBranch() });
    });

    it('issues no PUT while the form is invalid', () => {
      load();
      form().get('name')?.setValue('   ');
      fixture.detectChanges();

      submitForm();

      // afterEach's verify() asserts nothing was sent.
      expect(form().get('name')?.touched).toBeTrue();
    });

    it('shows a generic error and no Backend detail when the save fails validation', () => {
      load();
      submitForm();

      httpTesting.expectOne(BRANCH_URL).flush(
        { success: false, message: 'CameraId belongs to another branch.', errorCode: 'VALIDATION_ERROR' },
        { status: 400, statusText: 'Bad Request' },
      );
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be saved.');
      expect(text()).not.toContain('another branch');
      expect(text()).not.toContain('VALIDATION_ERROR');
    });

    it('shows the generic error for an unexpected server failure and preserves values', () => {
      load();
      form().patchValue({ name: 'Edited Name' });
      submitForm();

      httpTesting
        .expectOne(BRANCH_URL)
        .flush({ success: false, message: 'Boom.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be saved.');
      expect(text()).not.toContain('Boom.');
      // The Admin corrects and retries rather than re-editing everything.
      expect(form().get('name')?.value).toBe('Edited Name');
      expect((query('.branch-edit__submit') as HTMLButtonElement).disabled).toBeFalse();
    });

    it('treats a 404 at save time as a failure rather than a silent success', () => {
      const navigate = spyOn(router, 'navigateByUrl');
      load();
      submitForm();

      httpTesting
        .expectOne(BRANCH_URL)
        .flush({ success: false, errorCode: 'NOT_FOUND' }, { status: 404, statusText: 'Not Found' });
      fixture.detectChanges();

      expect(navigate).not.toHaveBeenCalled();
      expect(text()).toContain('The branch could not be saved.');
    });
  });

  describe('no device or key surface', () => {
    it('renders and requests no activation key, device id, or secret fields', () => {
      // The edit response is the ordinary read shape; even were unmodelled members present, the form
      // renders none of them (FS-03 §5.4, §12).
      load(placeholderBranch({
        device: { activationStatus: 'Activated', deviceId: '44444444-4444-4444-4444-444444444444' },
      }));

      const rendered = text().toLowerCase();
      for (const forbidden of [
        'activation key',
        'activationkey',
        'device id',
        'devicerecordid',
        'shared secret',
        'sharedsecret',
        'key status',
      ]) {
        expect(rendered).withContext(`edit form must not surface ${forbidden}`).not.toContain(
          forbidden,
        );
      }
    });

    it('sends only name, address, contactDetails, and cameras in the PUT body', () => {
      load();
      submitForm();

      const request = httpTesting.expectOne(BRANCH_URL);
      expect(Object.keys(request.request.body as object).sort()).toEqual([
        'address',
        'cameras',
        'contactDetails',
        'name',
      ]);

      request.flush({ success: true, data: placeholderBranch() });
    });
  });
});
