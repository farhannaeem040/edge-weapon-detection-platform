import { ComponentFixture, TestBed } from '@angular/core/testing';

import { WeaponClassBadgeComponent } from './weapon-class-badge';

describe('WeaponClassBadgeComponent', () => {
  let fixture: ComponentFixture<WeaponClassBadgeComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WeaponClassBadgeComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(WeaponClassBadgeComponent);
  });

  function render(className: string): void {
    fixture.componentRef.setInput('className', className);
    fixture.detectChanges();
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent?.trim() ?? '';
  }

  it('renders "Gun" for gun, case-insensitively', () => {
    render('GUN');
    expect(text()).toBe('Gun');
  });

  it('renders "Knife" for knife', () => {
    render('knife');
    expect(text()).toBe('Knife');
  });

  it('renders a defensive "Unknown class" for anything else, without echoing the raw value', () => {
    render('rifle');
    expect(text()).toBe('Unknown class');
    expect(text()).not.toContain('rifle');
  });

  it('never renders class information via colour alone — a visible label is always present', () => {
    render('gun');
    const badge = (fixture.nativeElement as HTMLElement).querySelector('.weapon-class-badge');
    expect(badge?.textContent?.trim().length).toBeGreaterThan(0);
  });
});
