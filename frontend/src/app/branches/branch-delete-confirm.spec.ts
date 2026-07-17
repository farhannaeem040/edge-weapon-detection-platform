import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { BranchDeleteConfirmComponent } from './branch-delete-confirm';

// A host so the required `branchName` input and the `deleting` input can be driven, and the outputs
// observed, exactly as the list and detail views drive them.
@Component({
  imports: [BranchDeleteConfirmComponent],
  template: `
    <app-branch-delete-confirm
      [branchName]="name()"
      [deleting]="deleting()"
      (confirmed)="confirmed = confirmed + 1"
      (cancelled)="cancelled = cancelled + 1"
    />
  `,
})
class HostComponent {
  readonly name = signal('Alpha Branch');
  readonly deleting = signal(false);
  confirmed = 0;
  cancelled = 0;
}

describe('BranchDeleteConfirmComponent', () => {
  let fixture: ComponentFixture<HostComponent>;
  let host: HostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HostComponent] }).compileComponents();
    fixture = TestBed.createComponent(HostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  function element(): HTMLElement {
    return fixture.nativeElement as HTMLElement;
  }

  function text(): string {
    return element().textContent ?? '';
  }

  it('is a labelled modal dialog', () => {
    const dialog = element().querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    // The label resolves to the heading, which names the branch.
    const labelledBy = dialog.getAttribute('aria-labelledby');
    expect(labelledBy).toBeTruthy();
    expect(element().querySelector(`#${labelledBy}`)?.textContent).toContain('Alpha Branch');
  });

  it('names the branch and warns the action cannot be undone', () => {
    expect(text()).toContain('Alpha Branch');
    expect(text()).toContain('cannot be undone');
  });

  it('states the dependent data that will be removed', () => {
    // FS-03 §6.3: cameras, Device registration, and Activation Keys.
    const rendered = text().toLowerCase();
    expect(rendered).toContain('cameras');
    expect(rendered).toContain('device registration');
    expect(rendered).toContain('activation keys');
  });

  it('states that a physical Agent is not remotely wiped', () => {
    // FS-03 §7.4: no remote Agent cleanup is claimed or performed.
    expect(text().toLowerCase()).toContain('not contacted or wiped');
  });

  it('emits cancelled from the Cancel action', () => {
    (element().querySelector('.delete-confirm__cancel') as HTMLButtonElement).click();
    expect(host.cancelled).toBe(1);
    expect(host.confirmed).toBe(0);
  });

  it('emits confirmed from the destructive Delete action', () => {
    (element().querySelector('.delete-confirm__delete') as HTMLButtonElement).click();
    expect(host.confirmed).toBe(1);
    expect(host.cancelled).toBe(0);
  });

  it('uses clear destructive wording on the confirm button', () => {
    expect(element().querySelector('.delete-confirm__delete')?.textContent).toContain(
      'Delete branch',
    );
  });

  it('moves focus to the safe Cancel action on open', () => {
    // A keyboard/screen-reader user lands inside the dialog on the non-destructive choice (FS-03 §8).
    expect(document.activeElement).toBe(element().querySelector('.delete-confirm__cancel'));
  });

  it('disables both actions while a delete is in flight', () => {
    host.deleting.set(true);
    fixture.detectChanges();

    expect((element().querySelector('.delete-confirm__cancel') as HTMLButtonElement).disabled).toBeTrue();
    expect((element().querySelector('.delete-confirm__delete') as HTMLButtonElement).disabled).toBeTrue();
    expect(element().querySelector('.delete-confirm__delete')?.textContent).toContain('Deleting');
  });

  it('renders no secret or internal identifier — only the branch name', () => {
    const rendered = text().toLowerCase();
    for (const forbidden of ['activationkey', 'devicerecordid', 'shared secret', 'deviceid']) {
      expect(rendered).not.toContain(forbidden);
    }
  });
});
