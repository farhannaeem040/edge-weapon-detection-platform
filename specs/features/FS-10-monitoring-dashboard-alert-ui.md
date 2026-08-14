# Feature Specification: Monitoring Dashboard & Alert-Management UI

| Field | Value |
|-------|-------|
| Feature ID | FS-10 |
| Title | Monitoring Dashboard & Alert-Management UI — Angular dashboard summary, paginated Alert list, and Alert detail views over new read-only Admin-authenticated Backend endpoints |
| Status | Draft — implementation in progress. |
| Related SRS Requirements | FR-DET-006 (status display, `New` only), FR-DET-007 (dashboard alert-detail fields — partial: timestamp, weapon type, confidence, branch, camera, snapshot-state; excludes live-stream-access, which this feature does not implement), FR-HLT-005 (device/camera visibility — partial: aggregate counts only, not a full health-telemetry page), FR-DET-013/BR-009 (Branch daily Alert quota display), NFR-SEC-001 (session-gated access) |
| Related Architecture Sections | ARCH-001 §10.5 (Angular feature-module structure — Alerts module, Device Health module named but until now undelivered), §14.1 (API contract table — `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}` rows), §9.3 (uniform `/api/v1` envelope convention) |
| Related ADRs | ADR-009 (API Design — versioning/envelope, reused unchanged); ADR-002 (amended, Security Architecture — JWT Bearer, reused unchanged); ADR-013 (Dashboard Session Revocation — reused unchanged, no auth mechanism changes) |
| Owner | Farhan Naeem |
| Dependencies | FS-01/IP-01 (Auth module — JWT/session mechanism this feature's routes are gated by, unchanged); FS-02/FS-03/IP-01/IP-03 (Angular foundation, shell, design tokens, Branch Management module — this feature extends the same shell and reuses the same `styles.css` token system); FS-06/IP-08 (Alert entity and backend sync — the data source this feature reads); FS-08/IP-10 (snapshot evidence capture — `SnapshotReference` is read, never written, by this feature); FS-09/IP-11 (Branch daily Alert quota — `BranchDailyAlertQuota`/`SuppressedDetectionEvent` are the data source for the quota card) |
| Explicitly excluded | Live video/streaming and `POST /api/v1/stream/authorize` (ARCH-001 §14.1 names this row; not implemented here); remote siren trigger/stop (FR-DET-010/012); Alert status transitions (FR-DET-008/009 — `Alert.Status` stays `New`; no Acknowledged/False Positive UI or `PATCH /api/v1/alerts/{id}/status` endpoint, since no approved specification for that transition exists yet); snapshot capture/upload (FS-08 territory, unchanged); a global Camera/Edge-Device fleet management screen; a multi-branch/site selector; analytics/trend charts or `GET /api/v1/reports`; any change to DeepStream, the Jetson Agent, the detection model, the daily Alert quota's enforcement logic, Data Protection, or Device credentials. |

---

## 1. Purpose and Scope

FS-06/FS-08/FS-09 give the platform a full detection→sync→persist→quota pipeline, but nothing today
lets an Admin see any of it — the Angular application currently manages only Branches. This feature
delivers the first real Alerts/Dashboard surface ARCH-001 §10.5 already names: a dashboard summary
(today's Alert count, the Branch daily quota, suppression totals, system counts), a server-side
paginated/filterable Alert list, and an Alert detail view — all read-only, all Admin-JWT-gated exactly
like the existing Branch Management module, all bounded to data the Backend actually has.

```text
Admin login (FS-01, unchanged)
    ↓
Dashboard summary  ←  GET /api/v1/dashboard/summary
    ↓
Alert list (paginated, filtered)  ←  GET /api/v1/alerts
    ↓
Alert detail  ←  GET /api/v1/alerts/{alertId}
    ↓
Snapshot section: placeholder (SnapshotReference is null in this increment, FS-08 §1)
```

### 1.1 What This Feature Adds

| Capability | Basis |
|---|---|
| `GET /api/v1/dashboard/summary` — bounded aggregate: Branch/timezone/local-date, today's accepted/max/remaining Alert counts, suppression totals (total/gun/knife), latest Alert timestamp, Device/Camera counts. | Vision §5.6 ("basic summary counts... may be included"), FS-09 quota data made visible for the first time. |
| `GET /api/v1/alerts` — server-side paginated, filterable (date range, class, Branch, Camera, Status, snapshot-availability), sortable (whitelisted fields), newest-first default. | ARCH-001 §14.1 (`GET /api/v1/alerts`, Dashboard, JWT — realized here for the first time). |
| `GET /api/v1/alerts/{alertId}` — single-Alert detail, 404 for unknown id, no internal storage paths or secrets. | ARCH-001 §14.1 (`GET /api/v1/alerts/{id}`). |
| Angular `dashboard/` module — replaces the existing thin shell-only `DashboardComponent` with a real summary view (quota progress, suppression counts, recent Alerts), bounded polling + manual refresh. | ARCH-001 §10.5 Alerts/Device Health modules; Stitch "Operations Overview" chrome (already implemented) gains real content. |
| Angular `alerts/` module — paginated/filterable list, URL-persisted filter state, and a detail view with a snapshot placeholder (never a broken image, never fabricated evidence). | Same. |
| Sidebar navigation gains **Dashboard** and **Alerts** as live items (both now backed by real data), per the existing `design/stitch/ANGULAR-IMPLEMENTATION-MAP.md` convention of only linking to real features. | `ANGULAR-IMPLEMENTATION-MAP.md` §3 constraint. |
| New `.table`/`.progress` CSS primitives added to the existing `styles.css` token system (first consumers) — no new colors, no UI framework. | Existing "Sentinel Operational System" design tokens, extended not replaced. |

### 1.2 What This Feature Does Not Touch

- No change to `AlertSyncService`, the `(DeviceId, EventId)` idempotency mechanism, or any Backend
  write path for Alerts — this feature is read-only.
- No change to the Branch daily Alert quota's enforcement logic (FS-09) — the dashboard only reads
  `BranchDailyAlertQuota`/`SuppressedDetectionEvent` rows; it never writes one.
- No change to `Alert.Status` or any lifecycle transition — every Alert this feature displays remains
  `New` (FS-06 §5.3); no PATCH endpoint or UI control to change it exists.
- No change to snapshot capture/upload (FS-08) — `SnapshotReference` is read as-is; when `null` (the
  case for every Alert in this increment, since snapshot capture is disabled in production), the UI
  renders a controlled placeholder, never a broken `<img>` or synthesized image.
- No change to Branch/Device/Camera CRUD (FS-02/FS-03) — the existing `branches/` module is untouched;
  device/camera detail continues to live there. This feature adds only aggregate counts to the
  dashboard, not a duplicate device management surface.
- No change to Admin authentication, JWT issuance, session revocation, or route-guard mechanics
  (FS-01) — new routes reuse the existing `authGuard`/`authInterceptor`/`sessionExpiryInterceptor`
  unchanged.

## 2. Actors

| Actor | Description |
|---|---|
| Admin User | The single authenticated account (FS-01, BR-001) viewing operational status and Alert history. |
| `AlertController` (new, Backend) | Thin, Admin-JWT-gated controller serving `GET /api/v1/alerts` and `GET /api/v1/alerts/{id}`. |
| `DashboardController` (new, Backend) | Thin, Admin-JWT-gated controller serving `GET /api/v1/dashboard/summary`. |
| `IAlertQueryService`/`AlertQueryService` (new, Backend Application/Infrastructure) | Owns pagination, filtering, sorting, and the Alert→Camera→Branch projection. |
| `IDashboardSummaryService`/`DashboardSummaryService` (new, Backend Application/Infrastructure) | Assembles the bounded aggregate summary from Branch/quota/Alert/Device/Camera data. |
| Angular `dashboard/` module (new) | Renders the summary view; polls on a bounded interval; manual refresh. |
| Angular `alerts/` module (new) | Renders the paginated/filterable list and the detail view. |

## 3. Preconditions

- FS-01 is deployed: Admin login/session/route-guard mechanism is operational.
- FS-06/IP-08 is deployed and enabled: Alerts exist in SQL Server.
- FS-09/IP-11 is deployed: `BranchDailyAlertQuota`/`SuppressedDetectionEvent` exist (may have zero rows
  for a given Branch/day — a valid, not-yet-populated state, not an error).

## 4. Functional Behavior

| Behavior | Requirement Basis |
|---|---|
| An authenticated Admin sees a dashboard summary showing today's Branch-local Alert count, configured maximum, remaining capacity, suppression totals (overall and per class), and the current Branch's local date/timezone. | FR-DET-013/BR-009, Vision §5.6 |
| An authenticated Admin can browse Alerts in a server-side paginated, filterable, sortable list, defaulting to the current Branch-local day, newest first. | ARCH-001 §14.1, Vision §5.6 |
| An authenticated Admin can open one Alert and see its detected/received timestamps, weapon class, confidence, Branch, Camera, Device, status, and snapshot state. | FR-DET-007 (partial — excludes live-stream-access) |
| When `SnapshotReference` is null, the Alert detail shows an explicit, professional placeholder — never a broken image, never synthesized evidence. | FS-08 §1, task constraint |
| Every dashboard/alert route requires a valid, non-revoked Admin session, exactly as every other protected route (FS-01, unchanged). | NFR-SEC-001 |
| Device-credentialed requests (the `X-Device-Id`/`X-Device-Secret` mechanism) cannot reach any new endpoint in this feature. | NFR-SEC-001, FS-06 §6.1 (device auth is a distinct mechanism, never accepted here) |
| More than 2,000 existing Alerts do not cause the browser to load, render, or freeze on the full set — pagination is server-side and bounded. | Task performance requirement |

## 5. Detailed Workflows

### 5.1 Dashboard Load

1. Admin navigates to `/dashboard` (now the default landing route after login, replacing the prior
   thin shell-only page).
2. Angular `DashboardService` calls `GET /api/v1/dashboard/summary` with the Admin's Bearer token.
3. `DashboardController` delegates to `IDashboardSummaryService`, which resolves the Branch's
   configured `TimeZoneId` (UTC fallback, FS-09 §4), the branch-local date, the `BranchDailyAlertQuota`
   row for that date (or a zero-state if none exists yet), the latest `Alert.DetectedAtUtc`, and
   `Device`/`Camera` counts.
4. Backend returns `DashboardSummaryDto` in the standard envelope.
5. The Angular view renders the quota progress, suppression totals, and system counts; a bounded
   polling loop (10–15s) refreshes the same call until the component is destroyed.

### 5.2 Alert List with Filters

1. Admin navigates to `/alerts` (default: current Branch-local day, newest first).
2. `AlertService` builds `HttpParams` from the active filter/pagination state and calls
   `GET /api/v1/alerts`.
3. `AlertQueryService` validates `sortBy` against a whitelist, clamps/validates `pageSize`, applies
   filters as SQL `WHERE` predicates (never in-memory post-filtering of an unbounded set), and returns
   a bounded page plus `totalCount`.
4. The Angular list renders rows and pagination controls; changing a filter updates the URL's query
   parameters, so a browser refresh or back-navigation reproduces the same filtered view.

### 5.3 Alert Detail

1. Admin opens one row (`/alerts/:alertId`).
2. `AlertService` calls `GET /api/v1/alerts/{alertId}`.
3. `AlertController` returns 404 (`ApiResponse.Fail("NOT_FOUND", "Alert not found.")`, matching
   `BranchController.GetById`'s convention exactly) for an unknown id, or the detail DTO otherwise.
4. The Angular detail view renders the metadata; the snapshot section renders the professional
   placeholder whenever `snapshotState !== 'available'`.

### 5.4 Unauthorized / Session Expiry

1. Any request to a new endpoint without a valid, non-revoked Admin session is rejected 401 by the
   existing fallback authorization policy (FS-01, unchanged — no new auth code is introduced).
2. The existing `sessionExpiryInterceptor` clears the local session and redirects to `/login`,
   unchanged, for any 401 from these new endpoints exactly as it already does for Branch endpoints.

### 5.5 Backend Unreachable

1. A network/5xx failure on the summary or list call leaves the previously loaded data on screen (never
   blanked) and shows a non-destructive retry banner.
2. Manual retry re-issues the same request; polling continues on its existing bounded schedule (no
   tightened retry loop).

## 6. Backend Responsibilities

- Register `AlertController` and `DashboardController` with no explicit `[Authorize]` attribute,
  matching `BranchController`'s convention — protection comes from the existing default/fallback
  Admin-session authorization policy (FS-01 §6, ADR-013), applied uniformly.
- `AlertQueryService`/`DashboardSummaryService` use `AsNoTracking()` reads and a single projected join
  per query (Alert → Camera → Branch by `CameraId`/`BranchId`) — no N+1 query pattern.
- Validate `sortBy` against a fixed whitelist (`detectedAtUtc` default, `receivedAtUtc`); reject an
  unrecognized value with a validation error, never silently substitute the default.
- Enforce a documented maximum `pageSize` (100); reject an oversized request with a validation error,
  never silently clamp.
- Never serialize `SnapshotReference`'s raw value, `DeviceRecordId`, `ProtectedSharedSecret`, an
  Activation Key, or any Data Protection material in any response this feature adds — the list/detail
  DTOs expose only a boolean/enum snapshot-availability indicator.
- Resolve the Branch-local quota day using the exact same `TimeZoneInfo`-based conversion
  `AlertSyncService` already uses (FS-09 §4) — this feature never computes or duplicates quota logic,
  it only reads the row the Backend's own quota enforcement already wrote.
- A missing `BranchDailyAlertQuota` row for the current day is a valid zero-state response, not an
  error — distinguishable in the DTO from "unavailable" (see §9).

## 7. Angular Dashboard Responsibilities

- Add `dashboard/` (summary) and `alerts/` (list + detail) as flat sibling modules under
  `frontend/src/app/`, matching the existing `core`/`auth`/`branches`/`shared` convention exactly (no
  `features/` prefix).
- Reuse the existing `authGuard` on every new route; reuse the existing `authInterceptor`/
  `sessionExpiryInterceptor` unchanged — no new authentication code.
- Extend `shell.ts`'s navigation with **Dashboard** and **Alerts** only (both now real); do not add
  Devices/Cameras/Settings/Live-monitoring as navigation — those remain unbacked by data this feature
  does not add, consistent with `ANGULAR-IMPLEMENTATION-MAP.md`'s existing "no dead links" rule.
- Persist Alert-list filter/pagination state in the URL's query parameters so refresh and
  back-navigation reproduce the same view; never lose filter state on a poll tick.
- Render explicit loading/empty/error/unavailable states (never a bare `0` presented as a confirmed
  quota count when the Backend reports "not yet available").
- Never render a broken `<img>` or a fabricated snapshot for a null `SnapshotReference`.
- Contain no business logic beyond presentation, filtering/pagination parameter construction, and
  polling — mirrors FS-01 §7's "no authentication business logic" discipline, applied to this feature's
  own read-only surface.

## 8. Data Requirements

| Entity/Field | Purpose |
|---|---|
| `Alert.{AlertId, DeviceId, EventId, CameraId, DetectedAtUtc, ReceivedAtUtc, ClassId, ClassName, Confidence, Status, SnapshotReference}` | Source fields for the list/detail DTOs (existing, FS-06/FS-08 — read-only here). |
| `Branch.{BranchId, Name, TimeZoneId}` | Branch identity and quota-day timezone resolution (existing, FS-09). |
| `Camera.{CameraId, BranchId, Name}` | Camera display name, resolved via `Alert.CameraId` (existing, FS-03). |
| `Device.{DeviceRecordId, DeviceId, BranchId}` | Device count and (external `DeviceId` only, never `DeviceRecordId`) Alert→Device association where displayed (existing, FS-02). |
| `BranchDailyAlertQuota.{BranchId, LocalDate, AcceptedAlertCount, SuppressedDetectionCount, GunSuppressedCount, KnifeSuppressedCount}` | Quota-card source (existing, FS-09 — read-only here). |

No new entity, table, or column is introduced by this feature.

## 9. API Contract (Feature-Specification Level)

### 9.1 `GET /api/v1/dashboard/summary`

| Aspect | Detail |
|---|---|
| Auth required | Valid JWT + active `AdminSession` (default policy, unchanged) |
| Query parameters | None |
| Success response | Standard envelope; `data` shape: `{branch:{id,name,timeZoneId,localDate,nextQuotaResetAtUtc}, alerts:{today,configuredMaximum,remaining,latestAlertAtUtc}, suppressions:{total,gun,knife}, system:{deviceCount,cameraCount}}` |
| Success status code | 200 |
| No quota row for today | `alerts.today=0`, `suppressions.*=0` — a valid zero-state, not an error |
| Failure (unauthorized) | Standard error envelope, 401 |

### 9.2 `GET /api/v1/alerts`

| Aspect | Detail |
|---|---|
| Auth required | Valid JWT + active `AdminSession` |
| Query parameters | `page` (default 1), `pageSize` (default 25, max 100), `fromUtc`, `toUtc`, `className`, `branchId`, `cameraId`, `status`, `snapshotAvailable`, `sortBy` (`detectedAtUtc`\|`receivedAtUtc`), `sortDescending` (default true) |
| Success response | Standard envelope; `data` shape: `{items:[...], page, pageSize, totalCount, totalPages}` |
| Success status code | 200 |
| Invalid `sortBy` | 400, validation error, standard error envelope |
| `pageSize` above the documented maximum | 400, validation error |
| Failure (unauthorized) | 401, standard error envelope |

### 9.3 `GET /api/v1/alerts/{alertId}`

| Aspect | Detail |
|---|---|
| Auth required | Valid JWT + active `AdminSession` |
| Success response | Standard envelope; single Alert detail DTO — no `SnapshotReference` raw value, no internal ids |
| Success status code | 200 |
| Unknown `alertId` | 404, `ApiResponse.Fail("NOT_FOUND", "Alert not found.")` — matches `BranchController.GetById` |
| Failure (unauthorized) | 401, standard error envelope |

All responses use the uniform envelope (ARCH-001 §9.3/§14.3, ADR-009) — no exception applies to these
endpoints (no binary/signaling payload is introduced).

## 10. Security Rules

- Every endpoint this feature adds is Admin-JWT-gated by the existing default authorization policy —
  no `[AllowAnonymous]`, no new auth mechanism, no weakening of the existing policy.
- Device credentials (`X-Device-Id`/`X-Device-Secret`) are never accepted by these endpoints — they use
  a structurally different, unrelated authentication scheme (FS-06 §6.1) that this feature does not
  wire into the Admin JWT pipeline.
- `sortBy`/`status`/`className`/`snapshotAvailable` are validated against fixed whitelists server-side
  — never interpolated into raw SQL, never used to build a dynamic `ORDER BY` string.
- No response from this feature ever includes a Device shared secret, protected secret, Activation Key,
  Data Protection key material, internal filesystem/storage path, or `DeviceRecordId`.
- CORS remains as-is (none configured; same-origin only, ARCH-001/Program.cs unchanged) — this feature
  does not add or loosen any CORS policy.
- Existing nginx headers/CSP (if any) are preserved unchanged; this feature adds no new static assets
  requiring a CSP exception.

## 11. Validation Rules

- `page` must be a positive integer; invalid/missing defaults to 1.
- `pageSize` must be a positive integer not exceeding the documented maximum (100); a value above the
  maximum is rejected (400), never silently clamped.
- `sortBy`, when present, must be one of the whitelisted values; any other value is rejected (400).
- `fromUtc`/`toUtc`, when present, must be valid ISO-8601 UTC timestamps; a malformed value is rejected
  (400).
- `status`/`className`/`snapshotAvailable`, when present, must match a known value; an unrecognized
  value is rejected (400) rather than silently matching nothing.

## 12. Error Cases

| Case | Handling |
|---|---|
| Unauthenticated/expired/revoked-session request to any new endpoint | 401, standard error envelope, no business logic reached |
| Device-credentialed request to any new endpoint | 401 (Device headers do not satisfy the Admin JWT policy) |
| Unknown `alertId` | 404, `ApiResponse.Fail("NOT_FOUND", "Alert not found.")` |
| Invalid `sortBy` | 400, validation error |
| `pageSize` above maximum | 400, validation error |
| No `BranchDailyAlertQuota` row for the current local day | 200, zero-state values — not an error |
| Backend unreachable from the browser | Angular shows a non-destructive retry banner; previously loaded data is preserved |

## 13. Acceptance Criteria

| # | Acceptance Criterion | Traces To |
|---|---|---|
| AC-1 | An authenticated Admin sees a dashboard summary reflecting real Branch/quota/suppression/system data. | FR-DET-013/BR-009 |
| AC-2 | An unauthenticated or Device-credentialed request to any new endpoint is rejected 401. | NFR-SEC-001 |
| AC-3 | The Alert list is server-side paginated; more than 2,000 Alerts never causes the browser to load the full set. | Task performance requirement |
| AC-4 | Alert-list filters (date range, class, Branch, Camera, status, snapshot-availability) are enforced server-side and reflected in the URL. | §5.2 |
| AC-5 | An Alert detail view for an unknown id returns 404 via the existing error-envelope convention. | §9.3 |
| AC-6 | A null `SnapshotReference` renders a controlled placeholder, never a broken image or fabricated evidence. | FS-08 §1 |
| AC-7 | No response from this feature ever exposes a secret, key, or internal storage path. | §10 |

## 14. Test Scenarios

See IP-12 §1 for the full backend (24-item) and frontend (20-item) test lists, matching this feature's
task-brief phases exactly; not restated here to avoid drift between the two documents.

## 15. Out of Scope

Consistent with the header table's "Explicitly excluded" row and the task's own constraints:

- Live video/streaming, `POST /api/v1/stream/authorize`.
- Remote siren trigger/stop.
- Alert status transitions (Acknowledged/False Positive) and `PATCH /api/v1/alerts/{id}/status`.
- Snapshot capture/upload, or any snapshot download endpoint.
- A global Camera/Edge-Device fleet management screen.
- A multi-branch/site selector (production scope is one Branch; the UI does not hide the ability to
  scale, but does not build unneeded multi-tenant chrome either).
- Analytics/trend charts, `GET /api/v1/reports`.
- Any change to DeepStream, the Jetson Agent, the detection model, tracker, inference interval,
  confidence threshold, NMS, the daily Alert quota's enforcement logic, Data Protection, or Device
  credentials.

## 16. Traceability Matrix

| Requirement/Decision | Realized In This Feature |
|---|---|
| FR-DET-006 | §5.3, §8 (`Status` displayed, never mutated) |
| FR-DET-007 (partial) | §5.3, §9.3 (excludes live-stream-access) |
| FR-HLT-005 (partial) | §5.1, §9.1 (aggregate counts only) |
| FR-DET-013/BR-009 | §5.1, §9.1, AC-1 |
| NFR-SEC-001 | §5.4, §10, AC-2 |
| ARCH-001 §14.1 | §9 (realizes the named `GET /api/v1/alerts`/`{id}` rows) |
| ARCH-001 §10.5 | §7 (Alerts/Device-Health module structure) |
| ADR-009 | §9 (envelope/versioning reused) |
| ADR-002 (amended), ADR-013 | §10 (auth mechanism reused unchanged) |

## 17. Open Implementation Details (Deferred to Implementation Plan)

- Exact default/maximum `pageSize` values beyond the documented 25/100 (IP-12 may tune within the
  stated bounds).
- Exact polling interval within the 10–15s bounded range.
- Exact `.table`/`.progress` CSS primitive class names (IP-12/implementation detail, following the
  existing `.card`/`.badge` naming convention).
