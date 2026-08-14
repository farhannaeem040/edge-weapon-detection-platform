import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AlertStatusBadgeComponent } from './alert-status-badge';

describe('AlertStatusBadgeComponent', () => {
  let fixture: ComponentFixture<AlertStatusBadgeComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AlertStatusBadgeComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(AlertStatusBadgeComponent);
  });

  function render(status: string): void {
    fixture.componentRef.setInput('status', status);
    fixture.detectChanges();
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent?.trim() ?? '';
  }

  it('renders "New" for the New status', () => {
    render('New');
    expect(text()).toBe('New');
  });

  it('renders a defensive "Unknown status" for anything else, without echoing the raw value', () => {
    render('Acknowledged');
    expect(text()).toBe('Unknown status');
    expect(text()).not.toContain('Acknowledged');
  });

  it('never renders a "suppressed" state — Alerts and suppressed detections are distinct', () => {
    render('SuppressedByQuota');
    expect(text()).not.toContain('Suppressed');
  });
});
