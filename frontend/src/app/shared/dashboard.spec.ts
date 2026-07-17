import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';

import { environment } from '../../environments/environment';
import { AUTH_TOKEN_STORAGE_KEY, AuthService } from '../auth/auth.service';
import { authInterceptor } from '../core/auth.interceptor';
import { sessionExpiryInterceptor } from '../core/session-expiry.interceptor';
import { DashboardComponent } from './dashboard';

// A placeholder, not a real JWT.
const PLACEHOLDER_TOKEN = 'placeholder.token.value';
const LOGOUT_URL = `${environment.apiBaseUrl}/auth/logout`;
const PROTECTED_URL = `${environment.apiBaseUrl}/branches`;

describe('DashboardComponent logout', () => {
  let fixture: ComponentFixture<DashboardComponent>;
  let httpTesting: HttpTestingController;
  let router: jasmine.SpyObj<Router>;
  let authService: AuthService;

  beforeEach(async () => {
    router = jasmine.createSpyObj<Router>('Router', ['navigateByUrl'], { url: '/dashboard' });

    await TestBed.configureTestingModule({
      imports: [DashboardComponent],
      providers: [
        provideHttpClient(withInterceptors([authInterceptor, sessionExpiryInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: router },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    authService = TestBed.inject(AuthService);
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);

    fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpTesting.verify();
    sessionStorage.clear();
  });

  function clickLogout(): void {
    const button = (fixture.nativeElement as HTMLElement).querySelector(
      '.dashboard__logout',
    ) as HTMLButtonElement;
    button.click();
    fixture.detectChanges();
  }

  it('calls the Backend logout endpoint', () => {
    clickLogout();

    const request = httpTesting.expectOne(LOGOUT_URL);
    expect(request.request.method).toBe('POST');
    request.flush({ success: true, message: 'Logged out.' });
  });

  it('carries the Bearer token on the logout request', () => {
    clickLogout();

    // The token must still be attached — it is what names the AdminSession the Backend revokes.
    const request = httpTesting.expectOne(LOGOUT_URL);
    expect(request.request.headers.get('Authorization')).toBe(`Bearer ${PLACEHOLDER_TOKEN}`);
    request.flush({ success: true, message: 'Logged out.' });
  });

  it('clears local state after a successful logout', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });

    expect(authService.getToken()).toBeNull();
    expect(authService.isAuthenticated()).toBeFalse();
  });

  it('clears local state when the Backend rejects the logout', () => {
    clickLogout();
    httpTesting
      .expectOne(LOGOUT_URL)
      .flush(
        { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
        { status: 401, statusText: 'Unauthorized' },
      );

    expect(authService.getToken()).toBeNull();
  });

  it('clears local state when the Backend fails with a server error', () => {
    clickLogout();
    httpTesting
      .expectOne(LOGOUT_URL)
      .flush({ success: false, message: 'Failure.' }, { status: 500, statusText: 'Error' });

    expect(authService.getToken()).toBeNull();
  });

  it('clears local state when the Backend is unreachable', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).error(new ProgressEvent('network error'));

    expect(authService.getToken()).toBeNull();
  });

  it('redirects to /login after logout', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });

    expect(router.navigateByUrl).toHaveBeenCalledWith('/login');
  });

  it('redirects to /login even when the logout request fails', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).error(new ProgressEvent('network error'));

    expect(router.navigateByUrl).toHaveBeenCalledWith('/login');
  });

  it('no longer attaches the old token to later requests', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });

    // A request issued after logout goes out anonymously. (The token a user copied before logout
    // is not thereby invalidated — only the Backend's AdminSession revocation does that, and the
    // Backend answers 401 to the copy on that basis alone, FS-01 §10, AC-6.)
    TestBed.inject(HttpClient).get(PROTECTED_URL).subscribe({ error: () => undefined });

    const followUp = httpTesting.expectOne(PROTECTED_URL);
    expect(followUp.request.headers.has('Authorization')).toBeFalse();
    followUp.flush({ success: true, data: [] });
  });

  it('does not display the token or the logout response', () => {
    clickLogout();
    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).not.toContain(PLACEHOLDER_TOKEN);
    expect(text).not.toContain('Logged out.');
  });

  it('prevents duplicate logout requests while one is in flight', () => {
    clickLogout();
    clickLogout();
    clickLogout();

    // expectOne throws if a second logout request was issued.
    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });
  });
});
