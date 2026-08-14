# `alerts`

Alert list and detail views (FS-10 §6/§9.2/§9.3; IP-12 T-205–T-209).

## Contents

- `alert.models.ts` — TS interfaces mirroring `AlertListItemDto`/`AlertListResponseDto`/
  `AlertDetailDto` field-for-field, plus `AlertListFilter`/`defaultAlertListFilter()` — the list's
  filter/pagination state, round-tripped through the URL's query params.
- `alert.routes.ts` — the Alert route path constants (`/alerts`, `/alerts/:alertId`).
- `alert.service.ts` — `AlertService`, the client for `GET /api/v1/alerts` and
  `GET /api/v1/alerts/{id}`. Sends every filter/sort/page parameter to the Backend as a query-string
  value and renders the response as-is: no client-side filtering, sorting, or slicing, which is what
  keeps the list responsive with 2,000+ Alerts (FS-10 §13). A 404 on `getAlert` becomes `null`.
- `alert-list.ts` — `AlertListComponent`: server-side-paginated table with a filter form (date range,
  weapon class, snapshot availability). Filter/pagination state lives in the URL's query string, not
  component state — every change navigates and every fetch is driven by
  `ActivatedRoute.queryParamMap`, so a refresh or the back/forward button restores exactly what was on
  screen. Loading/failed/empty/loaded states are modelled explicitly (mirrors `branch-list.ts`).
- `alert-detail.ts` — `AlertDetailComponent`: one Alert's detection/source fields and its snapshot
  section (mirrors `branch-detail.ts`'s loading/notFound/failed/loaded state machine). Renders no
  live video, siren control, or status-transition action — none are backed by an approved contract for
  this feature (FS-10 §1, explicitly excluded).
- `alert-snapshot-placeholder.ts` — `AlertSnapshotPlaceholderComponent`: the "no snapshot evidence"
  card shown whenever `snapshotAvailable` is `false`. Its own component so a future real-evidence view
  only has to swap this one piece. Renders **no `<img>` element** — never a broken image, never
  fabricated CCTV imagery.

## Snapshot handling (FS-10 §7, explicit requirement)

Snapshot upload/capture is out of scope for this feature (FS-08 owns it). This module only reads the
boolean `snapshotAvailable` the Backend already computed; it never requests, renders, or infers a
snapshot image, and the raw `SnapshotReference` storage key never reaches the browser.

## Security constraints

- Read-only: this module issues no write request anywhere.
- `className`/`status`/`sortBy` filters only ever send values the Backend's own whitelist accepts
  (`AlertController`); nothing here builds a dynamic filter clause.
- The `authGuard` on `/alerts` and `/alerts/:alertId` is a UX control, not a security boundary
  (FS-01 §10) — the Backend independently validates the JWT and the non-revoked `AdminSession`.
