import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { AuthService } from '../auth/auth.service';
import { Branch } from '../branches/branch.models';
import { GlobalMonitoringComponent } from './global-monitoring';

const BRANCHES_URL = `${environment.apiBaseUrl}/branches`;
const CAMERAS_URL = (branchId: string) =>
  `${environment.apiBaseUrl}/branches/${branchId}/live-monitoring/cameras`;

function makeBranch(overrides: Partial<Branch> = {}): Branch {
  return {
    branchId: '11111111-1111-1111-1111-111111111111',
    name: 'Alpha Branch',
    address: '1 Example Street',
    contactDetails: 'ops@example.invalid',
    cameras: [],
    device: { activationStatus: 'Unactivated' },
    ...overrides,
  };
}

describe('GlobalMonitoringComponent', () => {
  let fixture: ComponentFixture<GlobalMonitoringComponent>;
  let httpTesting: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GlobalMonitoringComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { getToken: () => 'fake-jwt' } },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(GlobalMonitoringComponent);
  });

  afterEach(() => httpTesting.verify());

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('shows a loading state before the branch list arrives', () => {
    fixture.detectChanges();
    expect(element().textContent).toContain('Loading branches');
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [] });
  });

  it('shows "no branches" and issues no camera request when there are zero branches', () => {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [] });
    fixture.detectChanges();

    expect(element().textContent).toContain('No branches are available for monitoring.');
    httpTesting.expectNone((req) => req.url.includes('live-monitoring/cameras'));
    httpTesting.expectNone((req) => req.url.includes('/live-streams'));
  });

  it('shows a failure state when the branch list request fails', () => {
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: false }, { status: 500, statusText: 'Internal Server Error' });
    fixture.detectChanges();

    expect(element().textContent).toContain('Branches could not be loaded');
  });

  it('auto-selects the only branch and shows its name without a branch dropdown', () => {
    const branch = makeBranch();
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [branch] });
    fixture.detectChanges();

    expect(element().querySelector('.global-monitoring__branch-select')).toBeNull();
    expect(element().textContent).toContain('Alpha Branch');
    expect(element().querySelector('app-live-monitoring')).not.toBeNull();
    httpTesting.expectOne(CAMERAS_URL(branch.branchId)).flush({ success: true, data: [] });
  });

  it('offers a dropdown and defaults to the first branch when there are multiple', () => {
    const branchA = makeBranch({ branchId: 'a', name: 'Branch A' });
    const branchB = makeBranch({ branchId: 'b', name: 'Branch B' });
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [branchA, branchB] });
    fixture.detectChanges();

    const select = element().querySelector('.global-monitoring__branch-select');
    expect(select).not.toBeNull();
    const options = Array.from(select!.querySelectorAll('option')).map((o) => o.textContent?.trim());
    expect(options).toEqual(['Branch A', 'Branch B']);
    httpTesting.expectOne(CAMERAS_URL('a')).flush({ success: true, data: [] });
  });

  it('switching branch loads only the new branch\'s cameras and clears the old player', () => {
    const branchA = makeBranch({ branchId: 'a', name: 'Branch A' });
    const branchB = makeBranch({ branchId: 'b', name: 'Branch B' });
    fixture.detectChanges();
    httpTesting.expectOne(BRANCHES_URL).flush({ success: true, data: [branchA, branchB] });
    fixture.detectChanges();
    // monitoringAvailable/inferenceAvailable: false — these fixtures only need to prove the
    // Camera *name* is loaded/cleared correctly; false avoids LiveMonitoringComponent
    // auto-triggering its own POST /api/v1/live-streams, which is exercised in its own spec.
    httpTesting.expectOne(CAMERAS_URL('a')).flush({
      success: true,
      data: [{ cameraId: 'cam-a', name: 'Camera A', cameraKey: 'camera-a', monitoringAvailable: false, inferenceAvailable: false }],
    });
    fixture.detectChanges();
    expect(element().textContent).toContain('Camera A');

    const select = element().querySelector('.global-monitoring__branch-select') as HTMLSelectElement;
    select.value = 'b';
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    // The previous branch's camera must not still be shown under the new branch.
    expect(element().textContent).not.toContain('Camera A');
    httpTesting.expectOne(CAMERAS_URL('b')).flush({
      success: true,
      data: [{ cameraId: 'cam-b', name: 'Camera B', cameraKey: 'camera-b', monitoringAvailable: false, inferenceAvailable: false }],
    });
    fixture.detectChanges();
    expect(element().textContent).toContain('Camera B');
  });
});
