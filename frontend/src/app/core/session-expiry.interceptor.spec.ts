import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';

import { environment } from '../../environments/environment';
import { AUTH_TOKEN_STORAGE_KEY, AuthService } from '../auth/auth.service';
import { authInterceptor } from './auth.interceptor';
import { sessionExpiryInterceptor } from './session-expiry.interceptor';

const PLACEHOLDER_TOKEN = 'placeholder.token.value';
const PROTECTED_URL = `${environment.apiBaseUrl}/branches`;
const LOGIN_URL = `${environment.apiBaseUrl}/auth/login`;
const ACTIVATE_URL = `${environment.apiBaseUrl}/activate`;

// The Backend's authentication-failure envelope, reproduced exactly (AuthenticationFailure.cs).
const UNAUTHORIZED_ENVELOPE = {
  success: false,
  message: 'Authentication is required.',
  errorCode: 'UNAUTHORIZED',
};

describe('sessionExpiryInterceptor', () => {
  let http: HttpClient;
  let httpTesting: HttpTestingController;
  let router: jasmine.SpyObj<Router>;
  let authService: AuthService;

  beforeEach(() => {
    router = jasmine.createSpyObj<Router>('Router', ['navigateByUrl'], { url: '/dashboard' });

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor, sessionExpiryInterceptor])),
        provideHttpClientTesting(),
        { provide: Router, useValue: router },
      ],
    });

    http = TestBed.inject(HttpClient);
    httpTesting = TestBed.inject(HttpTestingController);
    authService = TestBed.inject(AuthService);
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
  });

  afterEach(() => {
    httpTesting.verify();
    sessionStorage.clear();
  });

  it('clears local state when a protected request returns 401', () => {
    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    httpTesting
      .expectOne(PROTECTED_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    expect(authService.getToken()).toBeNull();
    expect(authService.isAuthenticated()).toBeFalse();
  });

  it('redirects to /login when a protected request returns 401', () => {
    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    httpTesting
      .expectOne(PROTECTED_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    expect(router.navigateByUrl).toHaveBeenCalledOnceWith('/login');
  });

  it('issues no logout request of its own when handling a 401', () => {
    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    httpTesting
      .expectOne(PROTECTED_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    // The session is already gone; a logout call would only earn another 401 and recurse.
    httpTesting.expectNone(`${environment.apiBaseUrl}/auth/logout`);
  });

  it('rethrows the failure so the caller still handles its own error', () => {
    let errored = false;
    http.get(PROTECTED_URL).subscribe({ error: () => (errored = true) });

    httpTesting
      .expectOne(PROTECTED_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    expect(errored).toBeTrue();
  });

  it('leaves a login 401 to LoginComponent as an invalid-credentials result', () => {
    let errored = false;
    http.post(LOGIN_URL, {}).subscribe({ error: () => (errored = true) });

    httpTesting.expectOne(LOGIN_URL).flush(
      { success: false, message: 'The credential identifier or password is invalid.', errorCode: 'INVALID_CREDENTIALS' },
      { status: 401, statusText: 'Unauthorized' },
    );

    // A failed sign-in is not an expired session: no redirect, and no session state destroyed.
    expect(errored).toBeTrue();
    expect(router.navigateByUrl).not.toHaveBeenCalled();
    expect(authService.getToken()).toBe(PLACEHOLDER_TOKEN);
  });

  it('does not redirect-loop when a login attempt fails on the login view', () => {
    Object.defineProperty(router, 'url', { value: '/login', configurable: true });

    for (let attempt = 0; attempt < 3; attempt++) {
      http.post(LOGIN_URL, {}).subscribe({ error: () => undefined });
      httpTesting.expectOne(LOGIN_URL).flush(
        { success: false, message: 'The credential identifier or password is invalid.', errorCode: 'INVALID_CREDENTIALS' },
        { status: 401, statusText: 'Unauthorized' },
      );
    }

    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('does not treat an activation 401 as an Admin session failure', () => {
    // FS-01 §6: activation is the Agent's anonymous endpoint, authenticated by an Activation Key.
    // Its rejection concerns that key, never the Admin's unrelated session.
    http.post(ACTIVATE_URL, {}).subscribe({ error: () => undefined });

    httpTesting
      .expectOne(ACTIVATE_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    expect(authService.getToken()).toBe(PLACEHOLDER_TOKEN);
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('does not treat a 401 from an unrelated third-party origin as a session failure', () => {
    http.get('https://third-party.example.com/data').subscribe({ error: () => undefined });

    httpTesting
      .expectOne('https://third-party.example.com/data')
      .flush({}, { status: 401, statusText: 'Unauthorized' });

    expect(authService.getToken()).toBe(PLACEHOLDER_TOKEN);
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  for (const status of [403, 404, 409, 500]) {
    it(`does not clear the token or redirect on a ${status}`, () => {
      http.get(PROTECTED_URL).subscribe({ error: () => undefined });

      httpTesting
        .expectOne(PROTECTED_URL)
        .flush({ success: false, message: 'Failure.', errorCode: 'X' }, { status, statusText: 'Error' });

      // The session was accepted; something else failed. Logging the Admin out would destroy a
      // session that is still valid.
      expect(authService.getToken()).toBe(PLACEHOLDER_TOKEN);
      expect(router.navigateByUrl).not.toHaveBeenCalled();
    });
  }

  it('does not clear the token when a protected request fails at the network level', () => {
    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    httpTesting.expectOne(PROTECTED_URL).error(new ProgressEvent('network error'));

    // An unreachable Backend has not rejected the session.
    expect(authService.getToken()).toBe(PLACEHOLDER_TOKEN);
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('settles concurrent 401s into one stable logged-out state', () => {
    // Model the real router: once it navigates, `url` reflects the view now open. That is what
    // the interceptor's guard reads, so redundant redirects are visible here if they happen.
    let currentUrl = '/dashboard';
    Object.defineProperty(router, 'url', { get: () => currentUrl, configurable: true });
    router.navigateByUrl.and.callFake((url: string | unknown) => {
      currentUrl = String(url);
      return Promise.resolve(true);
    });

    const urls = [
      `${environment.apiBaseUrl}/branches`,
      `${environment.apiBaseUrl}/devices`,
      `${environment.apiBaseUrl}/branches/1`,
    ];
    for (const url of urls) {
      http.get(url).subscribe({ error: () => undefined });
    }

    // Three in-flight requests all come back 401 together, as they would when a session expires.
    for (const url of urls) {
      httpTesting
        .expectOne(url)
        .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });
    }

    expect(authService.getToken()).toBeNull();
    // One redirect, not three: the later 401s find /login already open.
    expect(router.navigateByUrl).toHaveBeenCalledOnceWith('/login');
    expect(currentUrl).toBe('/login');
  });

  it('stops attaching the cleared token to later requests', () => {
    http.get(PROTECTED_URL).subscribe({ error: () => undefined });
    httpTesting
      .expectOne(PROTECTED_URL)
      .flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });

    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    const followUp = httpTesting.expectOne(PROTECTED_URL);
    expect(followUp.request.headers.has('Authorization')).toBeFalse();
    followUp.flush(UNAUTHORIZED_ENVELOPE, { status: 401, statusText: 'Unauthorized' });
  });
});
