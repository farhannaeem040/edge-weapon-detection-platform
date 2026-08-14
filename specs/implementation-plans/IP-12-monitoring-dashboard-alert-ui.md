# Implementation Plan: Monitoring Dashboard & Alert-Management UI

| Field | Value |
|-------|-------|
| Plan ID | IP-12 |
| Title | Monitoring Dashboard & Alert-Management UI — Backend `AlertController`/`DashboardController`, Angular `dashboard/`/`alerts/` modules |
| Status | In progress. |
| Realizes | FS-10 |
| Governing Documents | FS-10, FS-01 (frozen — auth mechanism, reused unchanged), FS-06 (frozen — Alert entity/idempotency), FS-08 (frozen — snapshot fields, read-only), FS-09 (frozen — quota/suppression data, read-only), `design/stitch/SCREEN-INVENTORY.md`/`ANGULAR-IMPLEMENTATION-MAP.md` (design source) |
| Depends On | FS-01/IP-01, FS-02/FS-03/IP-01/IP-03, FS-06/IP-08, FS-09/IP-11 — all fully delivered |
| Task ID Range | **T-191 – T-225** |
| Owner | Farhan Naeem |
| Explicitly Excluded | Everything FS-10's header/§15 lists — live streaming, siren, Alert status transitions, snapshot capture/upload, a global device-fleet screen, analytics/reports, any change to DeepStream/Agent/model/quota-enforcement/Data Protection/Device credentials. Production deployment — this plan stops at Phase 19 (isolated validation) per the task brief. |

---

## 1. Task Breakdown

### Backend (.NET)

| Task | Description |
|---|---|
| T-191 | `IAlertQueryService` (Application) — `AlertListQuery`/`AlertPageResult`/`AlertListItemView`/`AlertDetailView` read models (FS-10 §6, §9.2/§9.3). |
| T-192 | `AlertQueryService` (Infrastructure) — `AsNoTracking()` projected join (Alert→Camera→Branch), whitelisted sort, page-size validation, filter predicates applied in SQL. |
| T-193 | `IDashboardSummaryService`/`DashboardSummaryService` — Branch/timezone/local-date resolution (reuses FS-09 §4's `TimeZoneInfo` conversion), quota row read (zero-state when absent), latest-Alert timestamp, Device/Camera counts. |
| T-194 | API DTOs: `AlertListItemDto`, `AlertDetailDto`, `AlertListResponseDto`, `DashboardSummaryDto` (FS-10 §9.1–§9.3 exact shapes; no secrets/internal paths). |
| T-195 | `AlertController` (`GET api/v1/alerts`, `GET api/v1/alerts/{id:guid}`) — no `[Authorize]` attribute, default policy applies (FS-01 convention); 404 via `ApiResponse.Fail("NOT_FOUND", ...)` matching `BranchController.GetById`. |
| T-196 | `DashboardController` (`GET api/v1/dashboard/summary`). |
| T-197 | DI registration (`DependencyInjection.cs`) for `IAlertQueryService`/`IDashboardSummaryService`, scoped. |
| T-198 | Backend unit tests — Phase 15 items 1–3, 8–9, 16–18 (auth-required via stub-service controller tests, pagination/sort/page-size shape, 404, no-secrets assertion). |
| T-199 | Backend integration tests against real SQL Server — Phase 15 items 4–7, 10–15, 19–24 (Branch-local date, next-reset, each filter, Device-credential rejection, N+1 query-count assertion, existing sync/quota/DataProtection suites green, EF no-pending-changes). |

### Frontend (Angular)

| Task | Description |
|---|---|
| T-200 | `dashboard/dashboard.models.ts` — TS interfaces mirroring `DashboardSummaryDto` exactly. |
| T-201 | `dashboard/dashboard.service.ts` — `getSummary()`, unwraps `ApiEnvelope` (mirrors `branch.service.ts`). |
| T-202 | `dashboard/dashboard-summary.ts` (+html/css/spec) — quota progress card, suppression totals, system counts, recent-Alerts snippet, loading/loaded/error/unavailable signals, manual refresh + bounded (10–15s) polling with `takeUntilDestroyed()`, last-refresh timestamp. Deletes the old thin `shared/dashboard.ts`. |
| T-203 | `alerts/alert.models.ts`, `alerts/alert.routes.ts` — mirrors `branch.models.ts`/`branch.routes.ts` conventions. |
| T-204 | `alerts/alert.service.ts` — `listAlerts(filters)` (builds `HttpParams`), `getAlert(id)`, 404→`null` mapping (mirrors `branch.service.ts`). |
| T-205 | `alerts/alert-list.ts` (+html/css/spec) — server-paginated table, filter form round-tripped through URL query params, default = Branch-local day + newest-first, `track` by `alertId`, clear-filters, bounded page size, loading/empty/error states. |
| T-206 | `alerts/alert-detail.ts` (+html/css/spec) — route-param load, loading/notFound/failed/loaded signals (mirrors `branch-detail.ts`), snapshot-placeholder child component. |
| T-207 | `shared/weapon-class-badge.ts`, `shared/alert-status-badge.ts` — icon+text (never color-only), reuse `.badge` primitive. |
| T-208 | `shell.ts` nav update — add Dashboard + Alerts as live items (mirrors existing Branches-only list). |
| T-209 | `app.routes.ts` update — `dashboard` becomes a `ShellComponent` child (replacing the old top-level thin route), `alerts`/`alerts/:alertId` added (literal-before-parameterized ordering preserved), landing redirect changed from `branches` to `dashboard`. |
| T-210 | `styles.css` additions — `.table`/`.table__head`/`.table__row`, `.progress`/`.progress__bar`, `.badge--gun`/`.badge--knife`/`.badge--unknown`/`.badge--suppressed` (existing tokens only, no new colors). |
| T-211 | Frontend unit tests — Phase 16 items 1–7, 11–15, 17–20 (auth redirect, dashboard render/quota states/suppressed render, empty/error/retry, polling lifecycle, null-snapshot placeholder, keyboard nav, existing suite green). |
| T-212 | Frontend unit tests — Phase 16 items 8–10, 16 (list pagination, filter→params, URL persistence, detail render). |

### Design documentation

| Task | Description |
|---|---|
| T-213 | Update `design/stitch/SCREEN-INVENTORY.md` — promote Operations Overview/Alerts Management/Alert Review rows from deferred to implemented, with explicit mockup-vs-contract deviation lists (same rigor as the existing Branch Details row). |
| T-214 | Update `design/stitch/ANGULAR-IMPLEMENTATION-MAP.md` — add sections 15–18 (Dashboard summary, Alert list, Alert detail/snapshot placeholder, Sidebar nav additions), same "Stitch source → Maps to → Apply → Preserve/Do NOT add" structure. |

### Verification and Sign-off

| Task | Description |
|---|---|
| T-215 | Backend: `dotnet build`/`dotnet test`/`dotnet list package --vulnerable --include-transitive`/EF pending-model-check — throwaway SQL Server container, never the running production stack. |
| T-216 | Frontend: `npm ci`, `tsc --noEmit`, `ng test`, `ng build --configuration production` + budget/bundle-size check. |
| T-217 | `docker compose config` + `docker compose build frontend backend` (build only, no `up`). |
| T-218 | Isolated end-to-end validation — isolated Backend + test SQL Server, ≥2,000 seeded test Alerts, verify login/dashboard/list-filters/detail/snapshot-placeholder/unauthorized/offline/responsive (task brief Phase 19). |
| T-219 | Final report (FS-10/IP-12 Phase 20) — stop before any production deployment. |

## 2. Frozen Contract Reference

The exact endpoint shapes, DTO field names, validation rules, and security rules are specified in
**FS-10 §6–§12** and are binding for this plan — this document does not restate them; implementers must
read FS-10 in full before starting T-191/T-200.

## 3. Acceptance Criteria (summary — full list is FS-10 §13 / task brief Phase 15/16)

- Every new endpoint is Admin-JWT-gated by the existing default policy; Device credentials are rejected.
- The Alert list is server-side paginated and remains responsive with 2,000+ Alerts.
- Filters are enforced server-side and reflected in the URL.
- An unknown `alertId` returns 404 via the existing error-envelope convention.
- A null `SnapshotReference` renders a controlled placeholder, never a broken image.
- No response exposes a secret, key, or internal storage path.
- No existing Branch/Auth/Sync/Quota/DataProtection test regresses.

## 4. Rollback

This feature adds only new, additive routes/services/DTOs/Angular modules — no existing endpoint,
entity, or behavior is modified. Rollback is a plain revert of the added files/routes; no migration, no
data change, no feature flag is required (there is nothing stateful to roll back — every new endpoint
is read-only).
