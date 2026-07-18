import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  AfterViewInit,
  OnDestroy,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';

/**
 * The branch-deletion confirmation dialog (IP-03 T-46; FS-03 §6.3, §10.2, AC-13).
 *
 * Deleting a branch is a hard delete and cannot be undone (FS-03 §5.5), so it never happens on a
 * single click: this dialog is the explicit second step. It owns no HTTP and no state — the parent
 * (the list or the detail view) opens it, performs the delete when `confirmed` fires, and closes it
 * on `cancelled`. Keeping the request in the parent is what lets one dialog serve both places
 * without duplicating the call.
 *
 * It spells out exactly what deletion does, in the branch's own name, so the Admin is deciding about
 * a specific branch and its specific consequences (FS-03 §6.3): its cameras, its Device registration,
 * and its Activation Keys are removed, and — the one thing this does *not* do — a physical Agent is
 * not remotely wiped (FS-03 §7.4). No credential or internal identifier appears here; the dialog is
 * given only a name.
 *
 * Accessibility: it is a labelled modal dialog (`role="dialog"`, `aria-modal`, `aria-labelledby`) on
 * a backdrop that blocks interaction with the page behind it. Focus is moved to the non-destructive
 * Cancel action on open, so a keyboard or screen-reader user lands inside the dialog on the safe
 * choice rather than the destructive one; Escape cancels (safe, since cancelling sends nothing); and
 * focus is returned to whatever opened the dialog once it closes.
 */
@Component({
  selector: 'app-branch-delete-confirm',
  template: `
    <div class="delete-confirm__backdrop" (keydown.escape)="onEscape()">
      <div
        class="delete-confirm card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-confirm-heading"
        aria-describedby="delete-confirm-effects"
      >
        <h3 id="delete-confirm-heading" class="delete-confirm__heading">
          Delete branch “{{ branchName() }}”?
        </h3>

        <div id="delete-confirm-effects">
          <p class="delete-confirm__warning">
            This cannot be undone. Deleting this branch permanently removes:
          </p>
          <ul class="delete-confirm__effects">
            <li>its cameras;</li>
            <li>its Device registration;</li>
            <li>its Activation Keys.</li>
          </ul>
          <p class="delete-confirm__note">
            A physical Agent already installed at the branch is not contacted or wiped — only the
            Backend records are removed.
          </p>
        </div>

        <div class="delete-confirm__actions">
          <button
            #cancelButton
            class="delete-confirm__cancel btn btn--ghost"
            type="button"
            [disabled]="deleting()"
            (click)="cancelled.emit()"
          >
            Cancel
          </button>

          <!-- Destructive wording, and disabled while the request is in flight so a second click
               cannot issue a second delete (FS-03 §6.3, AC-13). -->
          <button
            class="delete-confirm__delete btn btn--danger"
            type="button"
            [disabled]="deleting()"
            (click)="confirmed.emit()"
          >
            {{ deleting() ? 'Deleting…' : 'Delete branch' }}
          </button>
        </div>
      </div>
    </div>
  `,
  styles: `
    .delete-confirm__backdrop {
      position: fixed;
      inset: 0;
      z-index: 50;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(23, 33, 28, 0.4);
      padding: var(--space-4);
    }

    .delete-confirm {
      max-width: 32rem;
      width: 100%;
      background: var(--color-surface);
      color: var(--color-text);
      box-shadow: var(--shadow-modal);
      padding: var(--space-5);
    }

    .delete-confirm__heading {
      margin-top: 0;
      margin-bottom: var(--space-3);
    }

    .delete-confirm__warning {
      margin: 0 0 var(--space-2);
      color: var(--color-text);
    }

    .delete-confirm__effects {
      margin: 0 0 var(--space-3);
      padding-left: 1.2rem;
      color: var(--color-text-muted);
      font-size: var(--text-sm);
    }

    .delete-confirm__note {
      margin: 0;
      font-size: var(--text-sm);
      color: var(--color-text-faint);
    }

    .delete-confirm__actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--space-2);
      margin-top: var(--space-5);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchDeleteConfirmComponent implements AfterViewInit, OnDestroy {
  /** The name of the branch being deleted — shown, never an id or any secret. */
  readonly branchName = input.required<string>();

  /** True while the parent's delete request is in flight; disables both buttons. */
  readonly deleting = input(false);

  readonly confirmed = output<void>();
  readonly cancelled = output<void>();

  private readonly cancelButton = viewChild.required<ElementRef<HTMLButtonElement>>('cancelButton');

  /** The control that opened the dialog, so focus can be returned to it on close (FS-03 §8). */
  private readonly opener = inject(DOCUMENT).activeElement as HTMLElement | null;

  ngAfterViewInit(): void {
    // Land the user on the safe, non-destructive action inside the dialog (FS-03 §8).
    this.cancelButton().nativeElement.focus();
  }

  ngOnDestroy(): void {
    // Return focus to whatever opened the dialog (the list/detail delete trigger), where practical,
    // so a keyboard user is not dropped at the top of the document after the dialog closes.
    this.opener?.focus?.();
  }

  /** Escape cancels — safe, because cancelling issues no request (FS-03 §6.3). */
  protected onEscape(): void {
    if (this.deleting()) {
      return;
    }

    this.cancelled.emit();
  }
}
