import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

/** The outcome of the most recent copy attempt; `idle` until the Admin presses the button. */
type CopyState = 'idle' | 'copied' | 'failed';

/**
 * The single disclosure of a branch's complete plaintext Activation Key (IP-01 T-27; FS-02 §5.1
 * step 7, §7).
 *
 * The Backend returns the key exactly once, at generation, and can never re-derive it — it stores
 * only the `keyId` and a salted hash of the `secret` (FS-02 §1.4, §11). This component is therefore
 * the last place the value exists, and its design follows from that:
 *
 *  - The key arrives as an input and is rendered. It is never written to `sessionStorage`,
 *    `localStorage`, IndexedDB, a cookie, the URL, a query parameter, or router state; it is never
 *    logged or sent to analytics; and it is never put in a link's href. It is held only by the
 *    parent's in-memory signal, which dies with the component.
 *  - Nothing here re-fetches it. There is no endpoint that would answer, by design.
 *  - Copying happens only when the Admin presses the button. Writing a credential to the system
 *    clipboard is the Admin's decision, so it is never done automatically on render.
 *
 * The Admin is told plainly that this is the only time the key is shown, because the recovery for
 * missing it is regeneration (T-28), not a second look.
 */
@Component({
  selector: 'app-activation-key-display',
  template: `
    <section class="activation-key">
      <h3>Activation Key</h3>

      <p class="activation-key__warning" role="alert">
        This Activation Key is shown once and cannot be retrieved again. Copy it now and store it
        securely; give it to the installer through a trusted channel.
      </p>

      <!-- The complete plaintext key (keyId.secret) and nothing else: no hash, no key id in
           isolation, no DeviceRecordId, no shared secret — none of which this component receives. -->
      <p class="activation-key__value">{{ activationKey() }}</p>

      @if (clipboardAvailable) {
        <button class="activation-key__copy" type="button" (click)="copy()">Copy key</button>
      } @else {
        <!-- No clipboard API (an insecure context, or an older browser): say so, rather than
             offering a button that cannot work. The key is on screen to copy by hand. -->
        <p class="activation-key__copy-unavailable">
          Copying is unavailable in this browser. Select the key above and copy it manually.
        </p>
      }

      @if (copyState() === 'copied') {
        <p class="activation-key__copy-status activation-key__copy-status--ok" role="status">
          Activation Key copied to the clipboard.
        </p>
      } @else if (copyState() === 'failed') {
        <p class="activation-key__copy-status activation-key__copy-status--error" role="alert">
          The key could not be copied. Select it above and copy it manually.
        </p>
      }

      <!-- A button, not a link: navigation must not carry the key anywhere, and this leaves the
           disclosure only when the Admin says they have it. -->
      <button class="activation-key__continue" type="button" (click)="continued.emit()">
        Continue to branch
      </button>
    </section>
  `,
  styles: `
    .activation-key__value {
      font-family: monospace;
      overflow-wrap: anywhere;
    }

    .activation-key__copy-status {
      margin: 0.25rem 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ActivationKeyDisplayComponent {
  /** The complete plaintext key (`keyId.secret`), straight from the create response. */
  readonly activationKey = input.required<string>();

  /** The Admin has recorded the key and wants to move on to the created branch. */
  readonly continued = output<void>();

  protected readonly copyState = signal<CopyState>('idle');

  /**
   * `navigator.clipboard` is absent in insecure contexts and older browsers. Read once, at
   * construction, purely to decide whether to offer the button.
   */
  protected readonly clipboardAvailable =
    typeof navigator !== 'undefined' && navigator.clipboard !== undefined;

  /**
   * Copies the key to the clipboard, in response to the Admin's click and never otherwise.
   *
   * Both outcomes are reported on screen: a silent failure would leave the Admin believing they hold
   * a credential they cannot get back. The rejection reason is deliberately not surfaced or
   * logged — the failed write's argument is the key itself.
   */
  protected copy(): void {
    if (!this.clipboardAvailable) {
      return;
    }

    navigator.clipboard.writeText(this.activationKey()).then(
      () => this.copyState.set('copied'),
      () => this.copyState.set('failed'),
    );
  }
}
