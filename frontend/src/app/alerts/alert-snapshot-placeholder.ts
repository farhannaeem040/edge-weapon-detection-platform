import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The Alert-detail snapshot section when no snapshot evidence is available (FS-10 §7 — snapshot
 * upload/capture is explicitly out of scope for this feature, and `snapshotAvailable` may be `false`
 * for any Alert regardless).
 *
 * This is its own component, not an inline `@else` branch in `alert-detail.ts`, precisely so a future
 * real-evidence view only has to swap this one piece. It renders **no `<img>` element at all** — never
 * a broken image, never a placeholder icon dressed up as a photo, and never any fabricated CCTV
 * imagery — only a plain, professional statement of fact plus the reason, so an Admin is never left
 * wondering whether something failed to load.
 *
 * The second line (manual-review Finding 2) states the *operational* reason — capture is currently
 * disabled platform-wide — without exposing how that is configured: no environment-variable name
 * (`WDA_SNAPSHOT_CAPTURE_ENABLED`), no file path, and no other internal implementation detail. It is
 * deliberately generic enough to stay true if the reason ever changes to "unresolved hardware
 * correlation" (FS-08/IP-10) rather than "disabled by configuration" — an Admin needs to know *that*
 * no evidence exists, not the mechanism.
 */
@Component({
  selector: 'app-alert-snapshot-placeholder',
  template: `
    <div
      class="snapshot-placeholder"
      role="img"
      aria-label="Snapshot evidence is not available for this Alert. Snapshot capture is currently not enabled."
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
      <p class="snapshot-placeholder__reason">Snapshot capture is currently not enabled.</p>
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

    .snapshot-placeholder__reason {
      margin: 0;
      font-size: var(--text-sm);
      color: var(--color-text-faint);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AlertSnapshotPlaceholderComponent {}
