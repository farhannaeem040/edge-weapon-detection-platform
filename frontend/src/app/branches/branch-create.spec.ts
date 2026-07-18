import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormArray, FormGroup } from '@angular/forms';
import { Router, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { BranchCreateComponent } from './branch-create';
import { CreateBranchRequest, CreatedBranch } from './branch.models';

// Every value in this suite is synthetic. The Activation Key below is a placeholder in the
// Backend's `keyId.secret` shape (FS-02 §1.4) and the RTSP URLs point at `.invalid` hosts: no real
// key, credential, camera, or deployment value appears here.
const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const CREATED_BRANCH_ID = '11111111-1111-1111-1111-111111111111';
const PLACEHOLDER_ACTIVATION_KEY = 'placeholderkeyid.placeholdersecretvalue';
const PLACEHOLDER_RTSP_URL = 'rtsp://camera.example.invalid:554/stream1';
const SECOND_RTSP_URL = 'rtsp://camera.example.invalid:554/stream2';

/** The create response, exactly as `BranchResponseDto.ForCreate` serializes one. */
function createdBranchResponse(): CreatedBranch {
  return {
    branchId: CREATED_BRANCH_ID,
    name: 'Placeholder Branch',
    address: '1 Example Street, Placeholder City',
    contactDetails: 'placeholder@example.invalid',
    cameras: [
      {
        cameraId: '22222222-2222-2222-2222-222222222222',
        name: 'Front Entrance',
        rtspUrl: 'rtsp://camera.example.invalid:554/stream1',
        enabled: true,
      },
    ],
    device: { activationStatus: 'Unactivated' },
    activationKey: PLACEHOLDER_ACTIVATION_KEY,
  };
}

describe('BranchCreateComponent', () => {
  let fixture: ComponentFixture<BranchCreateComponent>;
  let component: BranchCreateComponent;
  let httpTesting: HttpTestingController;
  let router: Router;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BranchCreateComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(BranchCreateComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => httpTesting.verify());

  /** The component's form. Bracket access keeps the members protected to the application. */
  function form(): FormGroup {
    return component['form'] as unknown as FormGroup;
  }

  function cameras(): FormArray {
    return form().get('cameras') as FormArray;
  }

  function query(selector: string): HTMLElement | null {
    return fixture.nativeElement.querySelector(selector) as HTMLElement | null;
  }

  function queryAll(selector: string): HTMLElement[] {
    return Array.from(fixture.nativeElement.querySelectorAll(selector));
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  /** Fills the branch fields and the first camera with a valid synthetic submission. */
  function fillValidForm(): void {
    form().patchValue({
      name: 'Placeholder Branch',
      address: '1 Example Street, Placeholder City',
      contactDetails: 'placeholder@example.invalid',
    });
    cameras().at(0).patchValue({ name: 'Front Entrance', rtspUrl: PLACEHOLDER_RTSP_URL });
    fixture.detectChanges();
  }

  function submitForm(): void {
    (query('.branch-create__form') as HTMLFormElement).dispatchEvent(
      new Event('submit', { bubbles: true }),
    );
    fixture.detectChanges();
  }

  describe('camera FormArray', () => {
    it('starts with exactly one camera', () => {
      // A branch requires at least one camera (FS-02 §12); the Admin should not have to add it.
      expect(cameras().length).toBe(1);
      expect(queryAll('app-camera-config-form').length).toBe(1);
    });

    it('adds a camera when the Admin asks for one', () => {
      query('.branch-create__add-camera')?.click();
      fixture.detectChanges();

      expect(cameras().length).toBe(2);
      expect(queryAll('app-camera-config-form').length).toBe(2);
    });

    it('removes a camera while more than one remains', () => {
      query('.branch-create__add-camera')?.click();
      fixture.detectChanges();
      cameras().at(0).patchValue({ name: 'First', rtspUrl: PLACEHOLDER_RTSP_URL });
      cameras().at(1).patchValue({ name: 'Second', rtspUrl: SECOND_RTSP_URL });

      queryAll('.camera__remove')[0].click();
      fixture.detectChanges();

      expect(cameras().length).toBe(1);
      // The surviving row is the one that was not removed, not merely "a row".
      expect(cameras().at(0).getRawValue().name).toBe('Second');
    });

    it('offers no remove control for the final remaining camera', () => {
      expect(cameras().length).toBe(1);
      expect(query('.camera__remove')).toBeNull();
    });

    it('refuses to remove the final camera even when asked directly', () => {
      // The button is not rendered, so this covers the guard behind it.
      component['removeCamera'](0);
      fixture.detectChanges();

      expect(cameras().length).toBe(1);
    });
  });

  describe('validation', () => {
    it('requires the branch name, address, and contact details', () => {
      expect(form().get('name')?.hasError('required')).toBeTrue();
      expect(form().get('address')?.hasError('required')).toBeTrue();
      expect(form().get('contactDetails')?.hasError('required')).toBeTrue();
      expect(form().invalid).toBeTrue();
    });

    it('rejects whitespace-only branch fields', () => {
      form().patchValue({ name: '   ', address: '  ', contactDetails: '\t ' });

      // Angular's own `required` accepts these; the Backend's [NotBlank] does not.
      expect(form().get('name')?.hasError('required')).toBeTrue();
      expect(form().get('address')?.hasError('required')).toBeTrue();
      expect(form().get('contactDetails')?.hasError('required')).toBeTrue();
    });

    it('requires a camera name', () => {
      cameras().at(0).patchValue({ name: '  ', rtspUrl: PLACEHOLDER_RTSP_URL });

      expect(cameras().at(0).get('name')?.hasError('required')).toBeTrue();
    });

    it('requires an RTSP URL', () => {
      cameras().at(0).patchValue({ name: 'Front Entrance', rtspUrl: '' });

      expect(cameras().at(0).get('rtspUrl')?.hasError('required')).toBeTrue();
    });

    it('rejects non-rtsp and relative camera URLs', () => {
      const rtspUrlControl = cameras().at(0).get('rtspUrl');

      for (const invalid of [
        'http://camera.example.invalid/stream1',
        'https://camera.example.invalid/stream1',
        '/streams/stream1',
        'camera.example.invalid/stream1',
      ]) {
        rtspUrlControl?.setValue(invalid);
        expect(rtspUrlControl?.hasError('rtspUrl'))
          .withContext(`expected rejection of a ${invalid.split(':')[0]} value`)
          .toBeTrue();
      }

      rtspUrlControl?.setValue(PLACEHOLDER_RTSP_URL);
      expect(rtspUrlControl?.valid).toBeTrue();
    });

    it('shows a generic RTSP error that never echoes the submitted URL', () => {
      const credentialBearingUrl = 'http://someuser:somepass@camera.example.invalid/stream1';
      cameras().at(0).patchValue({ name: 'Front Entrance', rtspUrl: credentialBearingUrl });
      cameras().at(0).get('rtspUrl')?.markAsTouched();
      fixture.detectChanges();

      expect(text()).toContain('Enter a valid RTSP URL.');
      // An RTSP URL may embed credentials; the error must not become the leak (FS-02 §11).
      expect(text()).not.toContain('somepass');
      expect(text()).not.toContain(credentialBearingUrl);
    });

    it('issues no HTTP request while the form is invalid', () => {
      submitForm();

      // afterEach's verify() asserts nothing was sent; the touched state is what the Admin sees.
      expect(form().get('name')?.touched).toBeTrue();
    });
  });

  describe('submission', () => {
    it('posts exactly the Backend contract fields, trimmed', () => {
      form().patchValue({
        name: '  Placeholder Branch  ',
        address: '1 Example Street, Placeholder City',
        contactDetails: 'placeholder@example.invalid',
      });
      cameras().at(0).patchValue({ name: '  Front Entrance ', rtspUrl: ` ${PLACEHOLDER_RTSP_URL} ` });
      fixture.detectChanges();

      submitForm();

      const request = httpTesting.expectOne(BRANCHES_URL);
      expect(request.request.method).toBe('POST');
      expect(request.request.body as CreateBranchRequest).toEqual({
        name: 'Placeholder Branch',
        address: '1 Example Street, Placeholder City',
        contactDetails: 'placeholder@example.invalid',
        cameras: [{ name: 'Front Entrance', rtspUrl: PLACEHOLDER_RTSP_URL }],
      });

      request.flush({ success: true, data: createdBranchResponse() });
    });

    it('posts every configured camera', () => {
      fillValidForm();
      query('.branch-create__add-camera')?.click();
      fixture.detectChanges();
      cameras().at(1).patchValue({ name: 'Back Entrance', rtspUrl: SECOND_RTSP_URL });
      fixture.detectChanges();

      submitForm();

      const request = httpTesting.expectOne(BRANCHES_URL);
      expect((request.request.body as CreateBranchRequest).cameras).toEqual([
        { name: 'Front Entrance', rtspUrl: PLACEHOLDER_RTSP_URL },
        { name: 'Back Entrance', rtspUrl: SECOND_RTSP_URL },
      ]);

      request.flush({ success: true, data: createdBranchResponse() });
    });

    it('prevents a duplicate submission while a request is in flight', () => {
      fillValidForm();

      submitForm();
      submitForm();
      submitForm();

      // Two branches, each with its own device and key, must not come from a double-click.
      const requests = httpTesting.match(BRANCHES_URL);
      expect(requests.length).toBe(1);

      requests[0].flush({ success: true, data: createdBranchResponse() });
    });

    it('disables the submit button while the request is in flight', () => {
      fillValidForm();
      submitForm();

      expect((query('.branch-create__submit') as HTMLButtonElement).disabled).toBeTrue();

      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: createdBranchResponse() });
    });

    it('shows a generic error and no Backend detail when creation fails validation', () => {
      fillValidForm();
      submitForm();

      httpTesting.expectOne(BRANCHES_URL).flush(
        {
          success: false,
          message: 'SQL error: constraint IX_Branches_Name violated at line 42.',
          errorCode: 'VALIDATION_ERROR',
        },
        { status: 400, statusText: 'Bad Request' },
      );
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be created.');
      expect(text()).not.toContain('SQL');
      expect(text()).not.toContain('IX_Branches_Name');
      expect(text()).not.toContain('VALIDATION_ERROR');
    });

    it('shows the same generic error for an unexpected server failure', () => {
      fillValidForm();
      submitForm();

      httpTesting
        .expectOne(BRANCHES_URL)
        .flush({ success: false, message: 'Boom.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      expect(text()).toContain('The branch could not be created.');
      expect(text()).not.toContain('Boom.');
    });

    it('preserves the entered values after a failed request', () => {
      fillValidForm();
      submitForm();

      httpTesting
        .expectOne(BRANCHES_URL)
        .flush({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      // The Admin corrects and retries; they do not retype the branch and every camera.
      expect(form().get('name')?.value).toBe('Placeholder Branch');
      expect(cameras().at(0).getRawValue().rtspUrl).toBe(PLACEHOLDER_RTSP_URL);
      expect((query('.branch-create__submit') as HTMLButtonElement).disabled).toBeFalse();
    });

    it('allows a retry after a failure', () => {
      fillValidForm();
      submitForm();
      httpTesting
        .expectOne(BRANCHES_URL)
        .flush({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });
      fixture.detectChanges();

      submitForm();

      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: createdBranchResponse() });
    });
  });

  describe('Activation Key disclosure', () => {
    /** Submits a valid form and answers with the Backend's create response. */
    function createSuccessfully(): void {
      fillValidForm();
      submitForm();
      httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: createdBranchResponse() });
      fixture.detectChanges();
    }

    it('displays the complete plaintext Activation Key after a successful creation', () => {
      createSuccessfully();

      expect(query('app-activation-key-display')).not.toBeNull();
      expect(text()).toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(text()).toContain('shown once');
    });

    it('replaces the form rather than navigating away', () => {
      const navigate = spyOn(router, 'navigateByUrl');

      createSuccessfully();

      // Navigating on success would destroy the key before the Admin could record it (FS-02 §5.1).
      expect(navigate).not.toHaveBeenCalled();
      expect(query('.branch-create__form')).toBeNull();
    });

    it('never writes the Activation Key to any browser storage', () => {
      createSuccessfully();

      expect(JSON.stringify(sessionStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(JSON.stringify(localStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(document.cookie).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    });

    it('never puts the Activation Key in the URL or a route parameter', () => {
      const navigate = spyOn(router, 'navigateByUrl');
      createSuccessfully();

      expect(window.location.href).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(router.url).not.toContain(PLACEHOLDER_ACTIVATION_KEY);

      query('.activation-key__continue')?.click();
      fixture.detectChanges();

      // The one navigation the flow performs carries a branch id and nothing else.
      const target = navigate.calls.mostRecent().args[0] as string;
      expect(target).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(target).toBe(`/branches/${CREATED_BRANCH_ID}`);
    });

    it('renders no link whose href carries the Activation Key', () => {
      createSuccessfully();

      for (const anchor of queryAll('a')) {
        expect(anchor.getAttribute('href') ?? '').not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      }
    });

    it('models no key hash, DeviceRecordId, ProtectedSharedSecret, or shared secret', () => {
      createSuccessfully();

      const rendered = text().toLowerCase();
      for (const forbidden of [
        'devicerecordid',
        'protectedsharedsecret',
        'shared secret',
        'secrethash',
        'hash',
      ]) {
        expect(rendered).withContext(`rendered output must not mention ${forbidden}`).not.toContain(
          forbidden,
        );
      }
    });

    it('navigates to the created branch detail route on "Continue to branch"', () => {
      const navigate = spyOn(router, 'navigateByUrl');
      createSuccessfully();

      query('.activation-key__continue')?.click();
      fixture.detectChanges();

      expect(navigate).toHaveBeenCalledOnceWith(`/branches/${CREATED_BRANCH_ID}`);
    });

    it('clears the in-memory key when the Admin continues', () => {
      spyOn(router, 'navigateByUrl');
      createSuccessfully();

      query('.activation-key__continue')?.click();
      fixture.detectChanges();

      expect(component['activationKey']()).toBeNull();
      expect(query('app-activation-key-display')).toBeNull();
      expect(text()).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    });

    it('clears the in-memory key when the component is destroyed', () => {
      createSuccessfully();

      fixture.destroy();

      expect(component['activationKey']()).toBeNull();
    });

    it('cannot recover the key on reinitialization, as a refresh would do', () => {
      createSuccessfully();
      fixture.destroy();

      // A refresh constructs the component afresh. Nothing was persisted, so there is nothing to
      // read back: the Admin sees an empty form and no key (FS-02 §6 — regeneration is the only
      // recovery, and that is T-28).
      const reloaded = TestBed.createComponent(BranchCreateComponent);
      reloaded.detectChanges();

      const reloadedText = (reloaded.nativeElement as HTMLElement).textContent ?? '';
      expect(reloadedText).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
      expect(reloaded.nativeElement.querySelector('app-activation-key-display')).toBeNull();
      expect(reloaded.nativeElement.querySelector('.branch-create__form')).not.toBeNull();
      // And it issues no request that could ask for one back.
      httpTesting.verify();
    });
  });
});
