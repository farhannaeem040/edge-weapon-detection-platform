import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { AUTH_TOKEN_STORAGE_KEY, AuthService } from './auth.service';

// Every credential and token in this file is an obvious placeholder. No real Admin credential,
// no real password, and no real JWT appears here or anywhere else in the test suite.
const PLACEHOLDER_IDENTIFIER = 'test-admin';
const PLACEHOLDER_PASSWORD = 'placeholder-password';
const PLACEHOLDER_TOKEN = 'placeholder.token.value';

describe('AuthService', () => {
  let service: AuthService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });

    service = TestBed.inject(AuthService);
    httpTesting = TestBed.inject(HttpTestingController);
    sessionStorage.clear();
  });

  afterEach(() => {
    httpTesting.verify();
    sessionStorage.clear();
  });

  function expectLoginRequest() {
    return httpTesting.expectOne(`${environment.apiBaseUrl}/auth/login`);
  }

  it('posts to the Backend login route', () => {
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe();

    const request = expectLoginRequest();
    expect(request.request.method).toBe('POST');
    request.flush({ success: true, data: { token: PLACEHOLDER_TOKEN } });
  });

  it('sends the credential field names the Backend contract defines', () => {
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe();

    const request = expectLoginRequest();
    // Matches the Backend's LoginRequestDto exactly — credentialIdentifier + password, nothing else.
    expect(request.request.body).toEqual({
      credentialIdentifier: PLACEHOLDER_IDENTIFIER,
      password: PLACEHOLDER_PASSWORD,
    });
    request.flush({ success: true, data: { token: PLACEHOLDER_TOKEN } });
  });

  it('extracts and stores the token from the standard envelope on success', () => {
    let completed = false;
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe({ complete: () => (completed = true) });

    expectLoginRequest().flush({
      success: true,
      message: null,
      data: { token: PLACEHOLDER_TOKEN },
    });

    expect(completed).toBeTrue();
    expect(sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBe(PLACEHOLDER_TOKEN);
  });

  it('does not store a token when login is rejected', () => {
    let errored = false;
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe({ error: () => (errored = true) });

    // The Backend's invalid-credentials envelope, verbatim in shape.
    expectLoginRequest().flush(
      { success: false, message: 'The credential identifier or password is invalid.', errorCode: 'INVALID_CREDENTIALS' },
      { status: 401, statusText: 'Unauthorized' },
    );

    expect(errored).toBeTrue();
    expect(service.getToken()).toBeNull();
    expect(service.isAuthenticated()).toBeFalse();
  });

  it('does not store a token when a success envelope carries no token', () => {
    let errored = false;
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe({ error: () => (errored = true) });

    expectLoginRequest().flush({ success: true, data: null });

    expect(errored).toBeTrue();
    expect(service.getToken()).toBeNull();
  });

  it('retrieves an already-stored token', () => {
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
    expect(service.getToken()).toBe(PLACEHOLDER_TOKEN);
  });

  it('reports local authentication state from token presence only', () => {
    expect(service.isAuthenticated()).toBeFalse();

    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);

    // Local state only — this says nothing about whether the Backend would accept the token.
    expect(service.isAuthenticated()).toBeTrue();
  });

  it('clears local authentication state when the token is cleared', () => {
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);

    service.clearToken();

    expect(service.getToken()).toBeNull();
    expect(service.isAuthenticated()).toBeFalse();
  });

  it('stores the token in sessionStorage and not in localStorage', () => {
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe();
    expectLoginRequest().flush({ success: true, data: { token: PLACEHOLDER_TOKEN } });

    // FS-01 §7: the token lives for the browser session only; no persistent login exists.
    expect(sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBe(PLACEHOLDER_TOKEN);
    expect(localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBeNull();
  });

  describe('logout', () => {
    beforeEach(() => {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
    });

    it('posts to the Backend logout route and clears local state on success', () => {
      service.logout().subscribe();

      const request = httpTesting.expectOne(`${environment.apiBaseUrl}/auth/logout`);
      expect(request.request.method).toBe('POST');
      request.flush({ success: true, message: 'Logged out.' });

      expect(service.getToken()).toBeNull();
    });

    it('clears local state even when the Backend rejects the logout', () => {
      service.logout().subscribe();

      httpTesting
        .expectOne(`${environment.apiBaseUrl}/auth/logout`)
        .flush(
          { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
          { status: 401, statusText: 'Unauthorized' },
        );

      // FS-01 §5.4 step 4 — the browser returns to a logged-out state regardless of the outcome.
      expect(service.getToken()).toBeNull();
    });

    it('clears local state when the Backend is unreachable', () => {
      service.logout().subscribe();

      httpTesting
        .expectOne(`${environment.apiBaseUrl}/auth/logout`)
        .error(new ProgressEvent('network error'));

      expect(service.getToken()).toBeNull();
    });

    it('attempts the Backend request before discarding the token', () => {
      // The token must still be present when the request is issued, otherwise the interceptor
      // would send an anonymous logout and the server-side AdminSession would never be revoked.
      service.logout().subscribe();

      expect(service.getToken()).toBe(PLACEHOLDER_TOKEN);

      httpTesting.expectOne(`${environment.apiBaseUrl}/auth/logout`).flush({ success: true });

      expect(service.getToken()).toBeNull();
    });
  });

  it('surfaces a rejection without exposing Backend error detail to the caller', () => {
    let error: unknown;
    service
      .login({ credentialIdentifier: PLACEHOLDER_IDENTIFIER, password: PLACEHOLDER_PASSWORD })
      .subscribe({ error: (err: unknown) => (error = err) });

    expectLoginRequest().flush(
      { success: false, message: 'The credential identifier or password is invalid.', errorCode: 'INVALID_CREDENTIALS' },
      { status: 401, statusText: 'Unauthorized' },
    );

    // The service passes the failure through; deciding what the user sees is the view's job.
    expect(error instanceof HttpErrorResponse).toBeTrue();
  });
});
