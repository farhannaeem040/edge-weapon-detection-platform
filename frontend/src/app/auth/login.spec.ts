import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { Subject, of, throwError } from 'rxjs';

import { AuthService } from './auth.service';
import { LoginComponent } from './login';

// Placeholders only — no real Admin credential, password, or JWT appears in this file.
const PLACEHOLDER_IDENTIFIER = 'test-admin';
const PLACEHOLDER_PASSWORD = 'placeholder-password';

describe('LoginComponent', () => {
  let fixture: ComponentFixture<LoginComponent>;
  let authService: jasmine.SpyObj<AuthService>;
  let router: jasmine.SpyObj<Router>;
  let httpTesting: HttpTestingController;

  beforeEach(async () => {
    authService = jasmine.createSpyObj<AuthService>('AuthService', ['login']);
    router = jasmine.createSpyObj<Router>('Router', ['navigateByUrl']);

    await TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: AuthService, useValue: authService },
        { provide: Router, useValue: router },
      ],
    }).compileComponents();

    httpTesting = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(LoginComponent);
    fixture.detectChanges();
  });

  afterEach(() => httpTesting.verify());

  function input(name: string): HTMLInputElement {
    const element = fixture.nativeElement as HTMLElement;
    return element.querySelector(`input[formControlName="${name}"]`) as HTMLInputElement;
  }

  function setValue(name: string, value: string): void {
    const field = input(name);
    field.value = value;
    field.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  function submitForm(): void {
    const element = fixture.nativeElement as HTMLElement;
    element.querySelector('form')!.dispatchEvent(new Event('submit'));
    fixture.detectChanges();
  }

  function fillValidCredentials(): void {
    setValue('credentialIdentifier', PLACEHOLDER_IDENTIFIER);
    setValue('password', PLACEHOLDER_PASSWORD);
  }

  it('renders the login form with both credential fields', () => {
    expect(input('credentialIdentifier')).not.toBeNull();
    expect(input('password')).not.toBeNull();
  });

  it('requires both fields before submitting', () => {
    setValue('credentialIdentifier', PLACEHOLDER_IDENTIFIER);
    submitForm();
    expect(authService.login).not.toHaveBeenCalled();

    setValue('password', PLACEHOLDER_PASSWORD);
    authService.login.and.returnValue(of(undefined));
    submitForm();
    expect(authService.login).toHaveBeenCalled();
  });

  it('issues no HTTP request for a blank form', () => {
    submitForm();

    expect(authService.login).not.toHaveBeenCalled();
    // httpTesting.verify() in afterEach independently proves no request escaped.
  });

  it('issues no HTTP request for whitespace-only values', () => {
    setValue('credentialIdentifier', '   ');
    setValue('password', '   ');
    submitForm();

    expect(authService.login).not.toHaveBeenCalled();
  });

  it('calls AuthService.login with the entered credentials on a valid submission', () => {
    authService.login.and.returnValue(of(undefined));
    fillValidCredentials();

    submitForm();

    expect(authService.login).toHaveBeenCalledOnceWith({
      credentialIdentifier: PLACEHOLDER_IDENTIFIER,
      password: PLACEHOLDER_PASSWORD,
    });
  });

  it('navigates to the protected landing route after a successful login', () => {
    authService.login.and.returnValue(of(undefined));
    fillValidCredentials();

    submitForm();

    expect(router.navigateByUrl).toHaveBeenCalledOnceWith('/branches');
  });

  it('shows a generic error for invalid credentials', () => {
    authService.login.and.returnValue(throwError(() => new Error('rejected')));
    fillValidCredentials();

    submitForm();

    const text = (fixture.nativeElement as HTMLElement).querySelector('.login__error')!.textContent!;
    expect(text).toContain('Login failed');
    // FS-01 §5.2/§11: the message must not disclose which of the two values was wrong.
    expect(text.toLowerCase()).not.toContain('identifier is');
    expect(text.toLowerCase()).not.toContain('unknown user');
    expect(text.toLowerCase()).not.toContain('incorrect password');
    expect(router.navigateByUrl).not.toHaveBeenCalled();
  });

  it('shows the same generic error for an unexpected server failure', () => {
    authService.login.and.returnValue(throwError(() => new Error('boom')));
    fillValidCredentials();

    submitForm();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('.login__error')!.textContent).toContain('Login failed');
  });

  it('renders the password field as a password input', () => {
    expect(input('password').type).toBe('password');
  });

  it('never renders the entered password as text anywhere in the view', () => {
    authService.login.and.returnValue(throwError(() => new Error('rejected')));
    fillValidCredentials();
    submitForm();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).not.toContain(PLACEHOLDER_PASSWORD);
    // The failed attempt's password is not left sitting in the control either.
    expect(input('password').value).toBe('');
  });

  it('prevents a duplicate submission while a request is in flight', () => {
    const pending = new Subject<void>();
    authService.login.and.returnValue(pending.asObservable());
    fillValidCredentials();

    submitForm();
    submitForm();
    submitForm();

    expect(authService.login).toHaveBeenCalledTimes(1);

    const element = fixture.nativeElement as HTMLElement;
    expect((element.querySelector('.login__submit') as HTMLButtonElement).disabled).toBeTrue();

    pending.next();
    pending.complete();
    fixture.detectChanges();

    // Once the request settles the form accepts input again.
    expect((element.querySelector('.login__submit') as HTMLButtonElement).disabled).toBeFalse();
  });

  it('allows a retry after a failed attempt', () => {
    authService.login.and.returnValue(throwError(() => new Error('rejected')));
    fillValidCredentials();
    submitForm();

    authService.login.and.returnValue(of(undefined));
    setValue('password', PLACEHOLDER_PASSWORD);
    submitForm();

    expect(authService.login).toHaveBeenCalledTimes(2);
    expect(router.navigateByUrl).toHaveBeenCalledOnceWith('/branches');
  });
});
