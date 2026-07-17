import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  AfterViewInit,
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
 * Accessibility: it is a labelled modal dialog (`role="dialog"`, `aria-modal`, `aria-labelledby`),
 * and focus is moved to the non-destructive Cancel action on open so a keyboard or screen-reader
 * user lands inside the dialog on the safe choice rather than on the destructive one.
 */
@Component({
  selector: 'app-branch-delete-confirm',
  template: `
    <div class="delete-confirm__backdrop">
      <div
        class="delete-confirm"
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
            class="delete-confirm__cancel"
            type="button"
            [disabled]="deleting()"
            (click)="cancelled.emit()"
          >
            Cancel
          </button>

          <!-- Destructive wording, and disabled while the request is in flight so a second click
               cannot issue a second delete (FS-03 §6.3, AC-13). -->
          <button
            class="delete-confirm__delete"
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
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.5);
      padding: 1rem;
    }

    .delete-confirm {
      max-width: 32rem;
      background: Canvas;
      color: CanvasText;
      border: 1px solid;
      border-radius: 0.5rem;
      padding: 1.25rem;
    }

    .delete-confirm__heading {
      margin-top: 0;
    }

    .delete-confirm__actions {
      display: flex;
      justify-content: flex-end;
      gap: 0.75rem;
      margin-top: 1rem;
    }

    .delete-confirm__cancel,
    .delete-confirm__delete {
      min-height: 2.5rem;
      padding: 0 1rem;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BranchDeleteConfirmComponent implements AfterViewInit {
  /** The name of the branch being deleted — shown, never an id or any secret. */
  readonly branchName = input.required<string>();

  /** True while the parent's delete request is in flight; disables both buttons. */
  readonly deleting = input(false);

  readonly confirmed = output<void>();
  readonly cancelled = output<void>();

  private readonly cancelButton = viewChild.required<ElementRef<HTMLButtonElement>>('cancelButton');

  ngAfterViewInit(): void {
    // Land the user on the safe, non-destructive action inside the dialog (FS-03 §8).
    this.cancelButton().nativeElement.focus();
  }
}
