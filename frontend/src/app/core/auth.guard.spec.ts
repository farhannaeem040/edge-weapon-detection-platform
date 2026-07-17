import { Location } from '@angular/common';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { Router, RouterOutlet, UrlTree, provideRouter } from '@angular/router';

import { routes } from '../app.routes';
import { AUTH_TOKEN_STORAGE_KEY } from '../auth/auth.service';
import { authGuard } from './auth.guard';

const PLACEHOLDER_TOKEN = 'placeholder.token.value';

@Component({ template: '<router-outlet />', imports: [RouterOutlet] })
class TestHost {}

describe('authGuard', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  describe('as a unit', () => {
    beforeEach(() => {
      TestBed.configureTestingModule({
        providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
      });
    });

    function runGuard(): boolean | UrlTree {
      return TestBed.runInInjectionContext(
        () => authGuard(null as never, null as never) as boolean | UrlTree,
      );
    }

    it('allows navigation when local token state exists', () => {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);

      expect(runGuard()).toBeTrue();
    });

    it('returns a UrlTree redirecting to /login when no token exists', () => {
      const result = runGuard();

      expect(result instanceof UrlTree).toBeTrue();
      expect((result as UrlTree).toString()).toBe('/login');
    });

    it('allows navigation on a token it cannot know is revoked (UX check only)', () => {
      // The guard reads browser storage and nothing else. That a revoked token passes it is
      // expected, not a defect: FS-01 §10 makes the Backend the authoritative check.
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, 'revoked.placeholder.token');

      expect(runGuard()).toBeTrue();
    });
  });

  describe('applied to the real routes', () => {
    let router: Router;
    let location: Location;

    beforeEach(() => {
      TestBed.configureTestingModule({
        providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter(routes)],
      });

      router = TestBed.inject(Router);
      location = TestBed.inject(Location);
    });

    it('redirects to /login when a protected route is opened without a token', fakeAsync(() => {
      TestBed.createComponent(TestHost);

      router.navigateByUrl('/dashboard');
      tick();

      expect(location.path()).toBe('/login');
    }));

    it('renders the protected route when local token state exists', fakeAsync(() => {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
      const fixture = TestBed.createComponent(TestHost);

      router.navigateByUrl('/dashboard');
      tick();
      fixture.detectChanges();

      expect(location.path()).toBe('/dashboard');
      expect((fixture.nativeElement as HTMLElement).textContent).toContain('Dashboard');
    }));

    it('redirects to /login when the branch list is opened without a token', fakeAsync(() => {
      TestBed.createComponent(TestHost);

      router.navigateByUrl('/branches');
      tick();

      expect(location.path()).toBe('/login');
    }));

    it('redirects to /login when a branch detail route is opened without a token', fakeAsync(() => {
      TestBed.createComponent(TestHost);

      router.navigateByUrl('/branches/11111111-1111-1111-1111-111111111111');
      tick();

      // The branch views are guarded individually, not merely by way of the shell they are reached
      // from — a deep link is exactly how a protected route gets opened cold (FS-01 AC-3).
      expect(location.path()).toBe('/login');
    }));

    it('redirects to /login when the branch create route is opened without a token', fakeAsync(() => {
      TestBed.createComponent(TestHost);

      router.navigateByUrl('/branches/new');
      tick();

      expect(location.path()).toBe('/login');
    }));

    it('renders the branch create route, not a branch detail, for /branches/new', fakeAsync(() => {
      sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, PLACEHOLDER_TOKEN);
      const fixture = TestBed.createComponent(TestHost);

      router.navigateByUrl('/branches/new');
      tick();
      fixture.detectChanges();

      // `new` shares a prefix with `/branches/:branchId`; the create route must win, or the Admin
      // gets a detail view fetching a branch called "new".
      expect(location.path()).toBe('/branches/new');
      expect((fixture.nativeElement as HTMLElement).textContent).toContain('Create branch');
    }));

    it('keeps /login reachable without a token', fakeAsync(() => {
      const fixture = TestBed.createComponent(TestHost);

      router.navigateByUrl('/login');
      tick();
      fixture.detectChanges();

      expect(location.path()).toBe('/login');
      expect(
        (fixture.nativeElement as HTMLElement).querySelector(
          'input[formcontrolname="credentialIdentifier"]',
        ),
      ).not.toBeNull();
    }));
  });
});
