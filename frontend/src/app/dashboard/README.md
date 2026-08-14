# `dashboard`

Admin monitoring dashboard (FS-10 §6; IP-12 T-200–T-203). Replaces the earlier thin placeholder that
lived at `shared/dashboard.ts` — `/dashboard` is now a real feature backed by the Backend's
read-only operational summary, not a static card.

## Contents

- `dashboard.models.ts` — TS interfaces mirroring the Backend's `DashboardSummaryDto` and its nested
  branch/alerts/suppressions/system DTOs field-for-field. Nothing here is ever posted back.
- `dashboard.service.ts` — `DashboardService`, the client for `GET /api/v1/dashboard/summary`. Unwraps
  the standard envelope; a 404 (no Branch configured yet) becomes `null` — a documented outcome, not a
  fault — and every other error propagates.
- `dashboard-summary.ts` — `DashboardSummaryComponent`: the quota card (usage count, progress bar,
  remaining/suppressed text, reset time), suppression/device/camera/latest-Alert stat tiles. Four
  states are modelled explicitly: `loading` (first fetch only), `failed` (Backend/network fault before
  any successful load), `unavailable` (no Branch configured — the Backend's documented 404), and
  `loaded`. A manual **Refresh** action and a 15s background poll share one fetch path; a poll tick or
  a failed manual refresh never blanks out an already-loaded summary or shows a spinner over it — the
  previous data and its "Updated HH:mm:ss" timestamp stay on screen. Polling stops automatically when
  the component is destroyed (`takeUntilDestroyed`), and no second poll can start while one is already
  running (`switchMap`).

## Quota-exhausted wording (FS-10 §6, explicit requirement)

When `alerts.remaining === 0`, the view renders "Daily quota reached. Further detections are being
suppressed." — never language implying a system failure or outage. Suppression is a working, designed
policy outcome (FS-09/IP-11), and the UI must not contradict that.

## Security constraints

- Read-only: this module issues no write request anywhere.
- No Device shared secret, Activation Key, or Data Protection material is a member of
  `DashboardSummaryDto` on the Backend, so there is nothing to redact here.
- The `authGuard` on `/dashboard` is a UX control, not a security boundary (FS-01 §10) — the Backend
  independently validates the JWT and the non-revoked `AdminSession` on the underlying call.
