import { ComponentFixture, TestBed } from '@angular/core/testing';

import { DeviceActivationStatus } from './branch.models';
import { DeviceStatusBadgeComponent } from './device-status-badge';

describe('DeviceStatusBadgeComponent', () => {
  let fixture: ComponentFixture<DeviceStatusBadgeComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DeviceStatusBadgeComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(DeviceStatusBadgeComponent);
  });

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  /** Renders the badge for a status. `unsafeStatus` exists for the values the type forbids. */
  function render(status: DeviceActivationStatus): void {
    fixture.componentRef.setInput('status', status);
    fixture.detectChanges();
  }

  /** Renders a value the Backend contract does not define — what a real response could still carry. */
  function renderUnsafe(status: string): void {
    fixture.componentRef.setInput('status', status as DeviceActivationStatus);
    fixture.detectChanges();
  }

  function label(): string {
    return element().querySelector('.device-status__label')?.textContent?.trim() ?? '';
  }

  it('renders the Unactivated state', () => {
    render('Unactivated');

    expect(label()).toBe('Unactivated');
  });

  it('renders the Activated state', () => {
    render('Activated');

    expect(label()).toBe('Activated');
  });

  it('renders the state named by the explicit status input', () => {
    render('Activated');
    expect(label()).toBe('Activated');

    // The same instance, told otherwise, follows the field rather than anything it kept.
    render('Unactivated');
    expect(label()).toBe('Unactivated');
  });

  it('takes no Device ID, so it cannot infer state from one', () => {
    // The proof is structural: `status` is the component's only input. There is no `deviceId` to
    // set, and a template binding to one would not compile.
    const inputs = Object.keys(fixture.componentInstance);

    expect(inputs).toContain('status');
    expect(inputs).not.toContain('deviceId');
  });

  it('renders a neutral Unknown state for a status outside the contract', () => {
    renderUnsafe('Decommissioned');

    expect(label()).toBe('Unknown');
  });

  it('does not read an unexpected status as Activated', () => {
    // Over-reporting activation is the one failure that would mislead an Admin about a live branch.
    for (const status of ['', 'activated', 'ACTIVATED', 'Pending', 'true']) {
      renderUnsafe(status);
      expect(label()).toBe('Unknown');
    }
  });

  it('never renders the raw unexpected value', () => {
    renderUnsafe('PLACEHOLDER-UNEXPECTED-STATUS');

    expect(element().textContent).not.toContain('PLACEHOLDER-UNEXPECTED-STATUS');
  });

  it('states each status in visible text, not colour alone', () => {
    // The label element carries no colour of its own; the text differs per state, so the badge
    // reads correctly with styles off or on a monochrome display (WCAG 1.4.1).
    render('Activated');
    const activated = label();

    render('Unactivated');
    const unactivated = label();

    expect(activated).toBe('Activated');
    expect(unactivated).toBe('Unactivated');
    expect(activated).not.toBe(unactivated);
  });

  it('distinguishes the states by more than a CSS class', () => {
    render('Activated');
    const activatedClasses = element().querySelector('.device-status')?.className ?? '';

    render('Unactivated');
    const unactivatedClasses = element().querySelector('.device-status')?.className ?? '';

    // The classes do differ — but stripping them still leaves two different words on screen.
    expect(activatedClasses).not.toBe(unactivatedClasses);
    expect(label()).toBe('Unactivated');
  });

  it('gives the status accessible context beyond the bare label', () => {
    render('Unactivated');

    const text = element().textContent ?? '';
    expect(element().querySelector('.device-status__context')?.textContent).toContain(
      'Device status:',
    );
    expect(text).toContain('has not been activated yet');
  });

  it('explains the Unknown state rather than leaving it bare', () => {
    renderUnsafe('Decommissioned');

    expect(element().textContent).toContain('could not be determined');
  });

  it('renders no Device ID, keys, or internal identifiers of its own', () => {
    render('Activated');

    const text = element().textContent ?? '';
    expect(text).not.toContain('Device ID');
    expect(text.toLowerCase()).not.toContain('key');
    expect(text.toLowerCase()).not.toContain('secret');
  });

  it('writes no status to browser storage', () => {
    // Cleared first so the assertion is about this component and not about what a neighbouring spec
    // left behind. A cached status would survive a reload and misreport a Device's real state.
    sessionStorage.clear();
    localStorage.clear();

    render('Activated');

    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });
});
