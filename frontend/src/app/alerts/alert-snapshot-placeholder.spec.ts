import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AlertSnapshotPlaceholderComponent } from './alert-snapshot-placeholder';

describe('AlertSnapshotPlaceholderComponent', () => {
  let fixture: ComponentFixture<AlertSnapshotPlaceholderComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AlertSnapshotPlaceholderComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(AlertSnapshotPlaceholderComponent);
    fixture.detectChanges();
  });

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  it('renders no <img> element — never a broken image', () => {
    expect(element().querySelector('img')).toBeNull();
  });

  it('states plainly that no snapshot is available, never as a system failure', () => {
    expect(element().textContent).toContain('Snapshot evidence is not available for this Alert.');
    expect(element().textContent).not.toContain('error');
    expect(element().textContent).not.toContain('failed');
  });

  it('states the operational reason without exposing internal configuration detail', () => {
    const text = element().textContent ?? '';
    expect(text).toContain('Snapshot capture is currently not enabled.');
    // No environment-variable name, file path, or other implementation detail (manual-review Finding 2).
    expect(text).not.toContain('WDA_SNAPSHOT');
    expect(text).not.toContain('/');
    expect(text).not.toContain('\\');
  });

  it('carries the same message to assistive technology via the group aria-label', () => {
    const group = element().querySelector('.snapshot-placeholder');
    expect(group?.getAttribute('aria-label')).toContain('not available for this Alert');
    expect(group?.getAttribute('aria-label')).toContain('currently not enabled');
  });
});
