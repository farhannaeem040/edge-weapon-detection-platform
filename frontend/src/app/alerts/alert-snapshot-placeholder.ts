import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The Alert-detail snapshot section when no snapshot evidence is available for this specific Alert
 * (FS-08 §12/IP-10 T-163) — an older Alert predating capture, a suppressed/rejected detection that
 * never captured a frame, or an upload that has not completed yet all render the same way here.
 *
 * This is its own component, not an inline `@else` branch in `alert-detail.ts`, precisely so the
 * real-evidence view only has to swap this one piece. It renders **no `<img>` element at all** —
 * never a broken image, never a placeholder icon dressed up as a photo, and never any fabricated
 * CCTV imagery — only a plain, professional statement of fact, so an Admin is never left wondering
 * whether something failed to load.
 *
 * Deliberately says nothing about *why* — not "capture is disabled" (misleading for the common case
 * where capture is enabled and this specific Alert simply has none) and no internal implementation
 * detail (no environment-variable name, no file path). An Admin needs to know *that* no evidence
 * exists for this Alert, not a mechanism that may not even apply to it.
 */
@Component({
  selector: 'app-alert-snapshot-placeholder',
  template: `
    <div
      class="snapshot-placeholder"
      role="img"
      aria-label="Snapshot evidence is not available for this Alert."
    >
      <svg
        class="snapshot-placeholder__icon"
        viewBox="0 0 24 24"
        width="32"
        height="32"
        aria-hidden="true"
        focusable="false"
      >
        <path
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M3 3l18 18"
        />
      </svg>
      <p class="snapshot-placeholder__text">Snapshot evidence is not available for this Alert.</p>
    </div>
  `,
  styles: `
    .snapshot-placeholder {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-3);
      padding: var(--space-7) var(--space-4);
      color: var(--color-text-faint);
      background: var(--color-surface-subtle);
      border: 1px dashed var(--color-border);
      border-radius: var(--radius);
      text-align: center;
    }

    .snapshot-placeholder__icon {
      color: var(--color-text-faint);
    }

    .snapshot-placeholder__text {
      margin: 0;
      font-size: var(--text-sm);
      color: var(--color-text-muted);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertSnapshotPlaceholderComponent {}
