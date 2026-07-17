import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../environments/environment';
import { AUTH_TOKEN_STORAGE_KEY } from '../auth/auth.service';
import { authInterceptor } from './auth.interceptor';

// A placeholder, not a real JWT.
const PLACEHOLDER_TOKEN = 'placeholder.token.value';
const PROTECTED_URL = `${environment.apiBaseUrl}/branches`;

describe('authInterceptor', () => {
  let http: HttpClient;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });

    http = TestBed.inject(HttpClient);
    httpTesting = TestBed.inject(HttpTestingController);
    sessionStorage.clear();
  });

  afterEach(() => {
    httpTesting.verify();
    sessionStorage.clear();
  });

  function storeToken(): void {
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
  }

  it('attaches the Bearer token to a Backend API request when a token exists', () => {
    storeToken();

    http.get(PROTECTED_URL).subscribe();

    const request = httpTesting.expectOne(PROTECTED_URL);
    expect(request.request.headers.get('Authorization')).toBe(`Bearer ${PLACEHOLDER_TOKEN}`);
    request.flush({ success: true, data: [] });
  });

  it('sends no Authorization header when no token exists', () => {
    http.get(PROTECTED_URL).subscribe();

    const request = httpTesting.expectOne(PROTECTED_URL);
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({ success: true, data: [] });
  });

  it('does not attach the token to an unrelated third-party origin', () => {
    storeToken();

    // Sending the session token to a foreign host would disclose it to whoever answers.
    http.get('https://third-party.example.com/api/v1/branches').subscribe();

    const request = httpTesting.expectOne('https://third-party.example.com/api/v1/branches');
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({});
  });

  it('does not attach the token to a third-party host exposing a lookalike API path', () => {
    storeToken();

    const lookalike = `https://evil.example.com${environment.apiBaseUrl}/branches`;
    http.get(lookalike).subscribe();

    const request = httpTesting.expectOne(lookalike);
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({});
  });

  it('does not attach the token to a same-origin non-API request', () => {
    storeToken();

    http.get('/assets/config.json').subscribe();

    const request = httpTesting.expectOne('/assets/config.json');
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({});
  });

  it('does not attach the token to the anonymous login endpoint', () => {
    // FS-01 §6: login is exempt from Admin JWT authentication — it is what issues a session.
    storeToken();

    http.post(`${environment.apiBaseUrl}/auth/login`, {}).subscribe();

    const request = httpTesting.expectOne(`${environment.apiBaseUrl}/auth/login`);
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({ success: true, data: { token: PLACEHOLDER_TOKEN } });
  });

  it('does not attach the token to the anonymous activation endpoint', () => {
    storeToken();

    http.post(`${environment.apiBaseUrl}/activate`, {}).subscribe();

    const request = httpTesting.expectOne(`${environment.apiBaseUrl}/activate`);
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({ success: true, data: {} });
  });

  it('attaches the token to the logout endpoint, which is protected', () => {
    // FS-01 §9.2: logout carries no [AllowAnonymous]; the token names the session to revoke.
    storeToken();

    http.post(`${environment.apiBaseUrl}/auth/logout`, null).subscribe();

    const request = httpTesting.expectOne(`${environment.apiBaseUrl}/auth/logout`);
    expect(request.request.headers.get('Authorization')).toBe(`Bearer ${PLACEHOLDER_TOKEN}`);
    request.flush({ success: true });
  });

  it('does not overwrite an Authorization header the caller set explicitly', () => {
    storeToken();

    http.get(PROTECTED_URL, { headers: { Authorization: 'Bearer caller-supplied' } }).subscribe();

    const request = httpTesting.expectOne(PROTECTED_URL);
    expect(request.request.headers.get('Authorization')).toBe('Bearer caller-supplied');
    request.flush({ success: true, data: [] });
  });

  it('does not log or otherwise expose the token', () => {
    const consoleSpies = [
      spyOn(console, 'log'),
      spyOn(console, 'info'),
      spyOn(console, 'warn'),
      spyOn(console, 'error'),
      spyOn(console, 'debug'),
    ];
    storeToken();

    http.get(PROTECTED_URL).subscribe();
    httpTesting.expectOne(PROTECTED_URL).flush({ success: true, data: [] });

    // FS-01 §10: JWTs are never written to logs.
    for (const spy of consoleSpies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });

  it('attaches whatever token is currently stored without validating it', () => {
    // The interceptor is not a validator: a revoked or forged token is still attached, and the
    // Backend is what rejects it (FS-01 §10). Nothing here is server-authoritative.
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, 'revoked.placeholder.token');

    http.get(PROTECTED_URL).subscribe({ error: () => undefined });

    const request = httpTesting.expectOne(PROTECTED_URL);
    expect(request.request.headers.get('Authorization')).toBe('Bearer revoked.placeholder.token');

    // The Backend independently answers 401 regardless of what the client believes.
    request.flush(
      { success: false, message: 'Authentication is required.', errorCode: 'UNAUTHORIZED' },
      { status: 401, statusText: 'Unauthorized' },
    );
  });
});
