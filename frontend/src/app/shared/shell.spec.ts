import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { environment } from '../../environments/environment';
import { AUTH_TOKEN_STORAGE_KEY, AuthService } from '../auth/auth.service';
import { authInterceptor } from '../core/auth.interceptor';
import { sessionExpiryInterceptor } from '../core/session-expiry.interceptor';
import { ShellComponent } from './shell';

const PLACEHOLDER_TOKEN = 'placeholder.token.value';
const LOGOUT_URL = `${environment.apiBaseUrl}/auth/logout`;

/**
 * Features with no implemented route: they must never appear as navigation (SCREEN-INVENTORY.md).
 * Dashboard and Alerts graduated to real navigation items with FS-10/IP-12 and are asserted
 * separately below, not here.
 */
const DEFERRED_NAV_LABELS = [
  'Live monitoring',
  'Incidents',
  'Cameras',
  'Edge devices',
  'Analytics',
  'System health',
  'Users and access',
  'Settings',
];

describe('ShellComponent', () => {
  let fixture: ComponentFixture<ShellComponent>;
  let httpTesting: HttpTestingController;
  let navigateByUrl: jasmine.Spy;
  let authService: AuthService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ShellComponent],
      providers: [
        provideHttpClient(withInterceptors([authInterceptor, sessionExpiryInterceptor])),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    }).compileComponents();

    navigateByUrl = spyOn(TestBed.inject(Router), 'navigateByUrl').and.resolveTo(true);
    httpTesting = TestBed.inject(HttpTestingController);
    authService = TestBed.inject(AuthService);
    sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);

    fixture = TestBed.createComponent(ShellComponent);
    fixture.detectChanges();
  });

  afterEach(() => {
    httpTesting.verify();
    sessionStorage.clear();
  });

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the rebranded product name', () => {
    expect(element().textContent).toContain('LJMU AI');
  });

  it('links to the dashboard, alerts, and branch list from the sidebar', () => {
    const links = Array.from(
      element().querySelectorAll('.shell__nav-link'),
    ) as HTMLAnchorElement[];
    const hrefs = links.map((link) => link.getAttribute('href'));
    const texts = links.map((link) => link.textContent ?? '');

    expect(hrefs).toEqual(['/dashboard', '/alerts', '/branches']);
    expect(texts.some((text) => text.includes('Dashboard'))).toBeTrue();
    expect(texts.some((text) => text.includes('Alerts'))).toBeTrue();
    expect(texts.some((text) => text.includes('Branches'))).toBeTrue();
  });

  it('renders no navigation for deferred, unimplemented features', () => {
    const nav = element().querySelector('.shell__nav')!.textContent ?? '';
    for (const label of DEFERRED_NAV_LABELS) {
      expect(nav).not.toContain(label);
    }
  });

  it('exposes exactly three primary navigation links', () => {
    expect(element().querySelectorAll('.shell__nav-link').length).toBe(3);
  });

  it('provides a router-outlet for the wrapped views', () => {
    expect(element().querySelector('router-outlet')).not.toBeNull();
  });

  it('gives the sign-out control an accessible name', () => {
    const button = element().querySelector('.shell__logout') as HTMLButtonElement;
    expect(button.tagName).toBe('BUTTON');
    expect(button.textContent).toContain('Sign out');
  });

  it('revokes the session and returns to login on sign out', () => {
    (element().querySelector('.shell__logout') as HTMLButtonElement).click();
    fixture.detectChanges();

    const request = httpTesting.expectOne(LOGOUT_URL);
    expect(request.request.method).toBe('POST');
    request.flush({ success: true, message: 'Logged out.' });

    expect(authService.getToken()).toBeNull();
    expect(navigateByUrl).toHaveBeenCalledWith('/login');
  });

  it('returns to login even when the logout request fails', () => {
    (element().querySelector('.shell__logout') as HTMLButtonElement).click();
    fixture.detectChanges();
    httpTesting.expectOne(LOGOUT_URL).error(new ProgressEvent('network error'));

    expect(authService.getToken()).toBeNull();
    expect(navigateByUrl).toHaveBeenCalledWith('/login');
  });

  it('prevents duplicate logout requests while one is in flight', () => {
    const button = element().querySelector('.shell__logout') as HTMLButtonElement;
    button.click();
    button.click();
    button.click();
    fixture.detectChanges();

    httpTesting.expectOne(LOGOUT_URL).flush({ success: true, message: 'Logged out.' });
  });

  it('toggles the responsive navigation open and closed', () => {
    const toggle = element().querySelector('.shell__nav-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('does not display the token', () => {
    expect(element().textContent ?? '').not.toContain(PLACEHOLDER_TOKEN);
  });
});
