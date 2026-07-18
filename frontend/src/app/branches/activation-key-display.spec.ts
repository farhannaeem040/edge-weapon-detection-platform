import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ActivationKeyDisplayComponent } from './activation-key-display';

// A synthetic placeholder in the Backend's `keyId.secret` shape (FS-02 §1.4). Never a real key.
const PLACEHOLDER_ACTIVATION_KEY = 'placeholderkeyid.placeholdersecretvalue';

describe('ActivationKeyDisplayComponent', () => {
  let fixture: ComponentFixture<ActivationKeyDisplayComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [ActivationKeyDisplayComponent] }).compileComponents();

    fixture = TestBed.createComponent(ActivationKeyDisplayComponent);
    fixture.componentRef.setInput('activationKey', PLACEHOLDER_ACTIVATION_KEY);
    fixture.detectChanges();
  });

  function query(selector: string): HTMLElement | null {
    return fixture.nativeElement.querySelector(selector) as HTMLElement | null;
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  it('displays the complete plaintext key', () => {
    // Complete: the Agent is configured with the whole `keyId.secret`, so a truncated or masked
    // rendering would be useless to the installer.
    expect(query('.activation-key__value')?.textContent?.trim()).toBe(PLACEHOLDER_ACTIVATION_KEY);
  });

  it('states that the key is shown only once and must be stored securely', () => {
    expect(text()).toContain('shown once');
    expect(text()).toContain('cannot be retrieved again');
    expect(text()).toContain('store it securely');
  });

  it('does not copy the key without a user action', () => {
    const writeText = spyOn(navigator.clipboard, 'writeText').and.resolveTo();

    fixture.detectChanges();

    // Writing a credential to the system clipboard is the Admin's decision, not a side effect of
    // rendering.
    expect(writeText).not.toHaveBeenCalled();
    expect(text()).not.toContain('copied to the clipboard');
  });

  it('copies the key to the clipboard when the Admin presses copy', async () => {
    const writeText = spyOn(navigator.clipboard, 'writeText').and.resolveTo();

    query('.activation-key__copy')?.click();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(writeText).toHaveBeenCalledOnceWith(PLACEHOLDER_ACTIVATION_KEY);
    expect(text()).toContain('Activation Key copied to the clipboard.');
  });

  it('reports a failed copy so the Admin knows to copy it manually', async () => {
    spyOn(navigator.clipboard, 'writeText').and.rejectWith(new Error('Clipboard unavailable.'));

    query('.activation-key__copy')?.click();
    await fixture.whenStable();
    fixture.detectChanges();

    // A silent failure would leave the Admin believing they hold a credential they cannot get back.
    expect(text()).toContain('The key could not be copied.');
    // The rejection reason is not surfaced — the failed write's argument was the key itself.
    expect(text()).not.toContain('Clipboard unavailable.');
  });

  it('emits the continue event only when the Admin presses continue', () => {
    let continued = 0;
    fixture.componentInstance.continued.subscribe(() => (continued += 1));

    fixture.detectChanges();
    expect(continued).toBe(0);

    query('.activation-key__continue')?.click();
    expect(continued).toBe(1);
  });

  it('renders no anchor carrying the key', () => {
    const anchors: HTMLAnchorElement[] = Array.from(fixture.nativeElement.querySelectorAll('a'));

    for (const anchor of anchors) {
      expect(anchor.getAttribute('href') ?? '').not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    }
  });

  it('writes the key to no browser storage', () => {
    query('.activation-key__copy')?.click();
    fixture.detectChanges();

    expect(JSON.stringify(sessionStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    expect(JSON.stringify(localStorage)).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
    expect(document.cookie).not.toContain(PLACEHOLDER_ACTIVATION_KEY);
  });

  // The wording T-27's creation flow relies on, which the T-28 inputs must not have disturbed.
  describe('by default, at creation', () => {
    it('uses the creation heading and continue label', () => {
      expect(query('h3')?.textContent).toContain('Activation Key');
      expect(query('.activation-key__continue')?.textContent).toContain('Continue to branch');
    });

    it('claims no previous key was invalidated, because creation invalidates none', () => {
      expect(query('.activation-key__regenerated')).toBeNull();
      expect(text()).not.toContain('no longer valid');
    });
  });

  describe('when disclosing a regenerated key (T-28)', () => {
    beforeEach(() => {
      fixture.componentRef.setInput('heading', 'New Activation Key');
      fixture.componentRef.setInput('regenerated', true);
      fixture.componentRef.setInput('continueLabel', 'Done');
      fixture.detectChanges();
    });

    it('labels the key as newly regenerated', () => {
      expect(query('h3')?.textContent).toContain('New Activation Key');
      expect(text()).toContain('This is a new Activation Key.');
    });

    it('states that the previous key is no longer valid', () => {
      expect(query('.activation-key__regenerated')?.textContent).toContain('no longer valid');
    });

    it('does not reveal whether the previous key had been used', () => {
      // Consumption state is the Backend's business and irrelevant to the Admin here; regeneration
      // invalidates the previous key either way (FS-02 §5.3 step 3).
      const rendered = text();
      expect(rendered).not.toContain('consumed');
      expect(rendered).not.toContain('unconsumed');
      expect(rendered).not.toContain('never used');
    });

    it('still shows the single-disclosure warning', () => {
      expect(text()).toContain('shown once');
      expect(text()).toContain('cannot be retrieved again');
    });

    it('offers the same explicit copy action, which still requires a click', async () => {
      const writeText = spyOn(navigator.clipboard, 'writeText').and.resolveTo();

      fixture.detectChanges();
      expect(writeText).not.toHaveBeenCalled();

      query('.activation-key__copy')?.click();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(writeText).toHaveBeenCalledOnceWith(PLACEHOLDER_ACTIVATION_KEY);
      expect(text()).toContain('Activation Key copied to the clipboard.');
    });

    it('uses the completion label for the leave button', () => {
      expect(query('.activation-key__continue')?.textContent).toContain('Done');
    });
  });
});
