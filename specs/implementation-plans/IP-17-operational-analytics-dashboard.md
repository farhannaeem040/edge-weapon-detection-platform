# Implementation Plan: Operational Analytics Dashboard

| Field | Value |
|-------|-------|
| Plan ID | IP-17 |
| Title | An Admin-only read-only analytics endpoint + CSV export over the existing `Alerts` table, and a Stitch-derived Angular Analytics page built from dependency-free inline-SVG charts |
| Realizes | FS-15 |
| Governing Documents | FS-15; FS-06 (frozen — `Alert` entity and its timestamps), FS-03 (frozen — `Branch`/`Camera`), FS-09 (frozen — `SuppressedDetectionEvent`), FS-10 (frozen — Admin shell, class whitelist, design tokens), FS-08 (frozen — snapshot fields) |
| Depends On | FS-03, FS-06, FS-08, FS-09, FS-10 — all delivered |
| Owner | Farhan Naeem |
| UI design source | Stitch MCP project `12701037052481013848` ("Sentinel AI Security Platform"), screen `03a107b0f10b4058adaf3ffcc4a2e3f7` — "Operational Analytics" (2560×2812 desktop). Layout adopted; branding, sample data, and shell deliberately not adopted. |
| Explicitly Excluded | Any EF Core migration. Any write path. Any Jetson/DeepStream/Agent/Bridge/MediaMTX change. An operator alert-review workflow. A server-side PDF subsystem. A charting library. |

---

## 1. Approved Design Decisions

1. **`Alert` is the unit of "detection".** There is no Backend `DetectionEvent` table (FS-15 §3.2). Quota-suppressed
   detections are reported separately and never folded into the detection count.

2. **The Stitch "Detection Accuracy / 98.4% Precision" donut is not implemented as designed.** `AlertStatus` has
   exactly one member (`New`) and 594/594 production rows carry it, so the database holds no ground truth. Inventing
   a review workflow, or deriving a percentage from a status that never changes, would both be fabrication. The card
   is replaced in-place by **Detection Confidence** (mean `Alert.Confidence` + high/medium/low band distribution),
   explicitly labelled as *not human-validated* (FS-15 §5.2).

3. **The Stitch "Avg. Response Time / Detection to operator validation" card is retitled, not repurposed.** No
   acknowledgement or resolution timestamp exists. `ReceivedAtUtc − DetectedAtUtc` is real and useful, but it is
   *delivery latency*, so the card is titled **Alert Delivery Latency** with the subtitle *Detection on the Jetson →
   receipt at the Backend* (FS-15 §5.4). Calling it response time would be a false claim.

4. **Latency samples are eligibility-filtered, and the exclusions are reported.** Production contains one
   store-and-forward backlog replay at 3.19 days that shifts the naive mean from ≈2.5 s to ≈467 s. The rule
   (`0 ≤ latency ≤ 1 day`) is stated in FS-15 §5.4, the excluded count is returned in the payload and shown on the
   card, and both mean and median are reported. Nothing is silently discarded.

5. **No charting library.** `frontend/package.json` carries only `@angular/*`, `rxjs`, `tslib`, `zone.js` — no UI
   framework at all, by standing project convention (`styles.css` header: *"No UI framework
   (Tailwind/Bootstrap/Material) and no font files are introduced"*). Four charts do not justify reversing that.
   Three small, focused, unit-testable inline-SVG components are added instead
   (`analytics-area-chart`, `analytics-bar-chart`, `analytics-donut-chart`), each rendering from a plain data array
   and using the existing CSS custom properties for colour. They are `OnPush`, `viewBox`-based, and therefore
   responsive without a resize observer.

6. **One endpoint, not N+1.** All four cards and the summary come from a single
   `GET /api/v1/analytics/operational` call. The Branch dropdown reuses the existing `BranchService.list()` the
   Alerts page already uses — no new Branch endpoint.

7. **All aggregation is SQL-side.** Every count / average / group-by is expressed as translatable LINQ over
   `AsNoTracking()` queries and executed by SQL Server. No query materialises the `Alerts` table into memory. The
   only in-memory work is zero-filling empty buckets, which operates on at most 90 rows.

8. **No migration.** Every column read already exists. The existing indexes (`IX_Alerts_DeviceId_EventId`,
   `IX_Alerts_DeviceId`, `IX_Alerts_CameraId`, `Cameras.BranchId`) plus the table's small production size (594 rows)
   are measured before any index is proposed (T-22). An index is added only if a measured plan justifies it.

9. **Generate report = print-optimised view.** No PDF library, no server-side rendering subsystem. A `@media print`
   presentation of the same real metrics plus `window.print()`. Real behaviour, no dead control, no new dependency
   (FS-15 §7).

10. **Authorization is inherited, not re-declared.** Both controllers omit `[AllowAnonymous]`, so the application's
    existing fallback `ActiveAdminSessionRequirement` policy applies — identical to `AlertController` and
    `DashboardController`. No new policy, no new scheme.

---

## 2. Stitch inspection record

Retrieved through Stitch MCP (`list_projects` → `list_screens` → screen HTML + screenshot), not guessed from an
image alone. Observed and mapped:

| Stitch element | Observed value | Adaptation in LJMU app |
|---|---|---|
| Page container | `p-8`, `max-w-[1440px]`, centred | Existing `.shell__content` (`--space-6` padding, `--layout-max: 1440px`) — already identical; reused, not re-declared |
| Header block | breadcrumb → `display-lg` title → `body-md` subtitle, actions right-aligned, `mb-8` | `.page-header` + `.breadcrumb` + `.page-header__title` primitives; `--space-6` bottom margin |
| Actions | outlined "Export CSV" (`border-primary text-primary`) + filled "Generate Report" (`bg-primary-container text-white`) | `.btn--secondary` and `.btn--primary` (same colours: `#146b3a`) |
| Filter card | `bg-surface-container-lowest p-6 rounded-xl border border-outline-variant shadow-sm`, `flex-wrap`, `gap-6`, refresh pushed by `ml-auto` | `.card` + `.card__body` (`#fff`, `--radius-md`, `--color-border`, `--shadow-card`), flex-wrap, `--space-5` gap, `margin-left:auto` refresh |
| Filter control | `label` (`label-sm`, muted) above a `select` with `rounded-lg`, 1px outline, `min-w-[180–200px]` | `.field` + `.field__label` + global `select` styling (already 8px radius, 1px `--color-border`, green focus ring) |
| Grid | `grid-cols-12 gap-6`; cards at `lg:col-span-8` / `4` / `6` / `6`, footer `col-span-12`; all collapse to `col-span-12` below `lg` (1024 px) | Native CSS Grid, 12 columns, `--space-5` gap, identical spans, `@media (max-width: 1024px)` collapse |
| Card | `rounded-xl` (12 px), 1px `#bfc9be`, `shadow-sm`, `p-6`; heading 18 px Geist + 12 px muted subtitle, `mb-6` | `.card` (12 px radius, `--color-border`, `--shadow-card`), `--space-5` padding, `--text-heading` (18 px) + `--text-label` (12 px) |
| Area chart | `h-[320px]`, tension 0.4, 3 px stroke `#146b3a`, gradient fill `rgba(20,107,58,0.3)→0`, y grid only, no point markers, hover point r=6 | `analytics-area-chart`: 320 px tall, Catmull-Rom→cubic smoothing, 3 px `--color-primary` stroke, `<linearGradient>` at the same two stops, horizontal grid lines only, hover point r=6 |
| Donut | `h-[220px] w-[220px]`, `cutout: '80%'`, centre `display-lg` bold value + `label-sm` primary caption, legend rows with 10 px dots and right-aligned counts | `analytics-donut-chart`: 220 px, 80 % cutout, same centred value/caption composition, same legend row layout |
| Bar chart | `h-[280px]`, `barThickness: 28`, `borderRadius: 4`, no grid lines, rotated x labels | `analytics-bar-chart`: 280 px, 28 px bars (clamped when many Branches), 4 px `rx`, no grid, rotated labels |
| Line chart | `h-[280px]`, 2 px stroke, point radius 4, `suggestedMin` | `analytics-area-chart` in line mode (`[filled]="false"`), 2 px stroke, r=4 points |
| Status footer | `bg-inverse-surface` (`#28332d`) charcoal bar, `rounded-xl`, `p-4`, `label-sm`, left cluster (status dot + last sync) / right cluster (metrics), `flex-wrap justify-between` | Same charcoal bar using `--color-charcoal` and `--color-text-on-dark-muted`, same two-cluster flex layout — but **only** fields the platform can actually supply (FS-15 §5.6) |
| Icon system | Material Symbols web font | **Not adopted** — the app ships no font files and uses inline stroke SVG; equivalent icons drawn in the existing 24×24, 1.8 stroke style |

**Deliberately not adopted:** the Stitch sidebar (Overview / Incidents / Cameras / Edge devices / System health /
Users and access / Settings / Profile / Collapse), the top app bar (search field, notification bell, help, locale,
avatar), the "Sentinel AI / Enterprise Security" brand block, and the "Alex Rivers / Chief Security Officer"
identity. The LJMU `ShellComponent` chrome is preserved untouched; only the **content area** is adapted.

---

## 3. Task Breakdown

### Backend

| Task | Description |
|---|---|
| T-1 | `IOperationalAnalyticsService` (Application) — request/read-model records: `AnalyticsQuery`, `AnalyticsRange`, `AnalyticsBucket`, `OperationalAnalyticsView`, `AnalyticsSummaryView`, `TimeBucketPoint`, `BranchCountPoint`, `LatencyBucketPoint`, `AnalyticsExportRow`, and the typed `AnalyticsOutcome` (`Ok` / `BranchNotFound`). |
| T-2 | `AnalyticsWindowResolver` (Infrastructure) — preset → `[fromUtc, toUtc)` + bucket size, bucket-start truncation (hour / UTC day / ISO Monday week), and label formatting. Pure and directly unit-testable. |
| T-3 | `OperationalAnalyticsService` (Infrastructure) — the SQL-side aggregations of FS-15 §5, `AsNoTracking()` throughout, zero-fill of empty buckets, and the export row projection. |
| T-4 | `AnalyticsCsvWriter` (Infrastructure) — RFC 4180 quoting + formula-injection defusing + the fixed column set of FS-15 §6.2. Pure and directly unit-testable. |
| T-5 | `AnalyticsController` (Api) — `GET /api/v1/analytics/operational` and `GET /api/v1/analytics/operational/export`; parameter validation per FS-15 §4; no `[AllowAnonymous]`. |
| T-6 | API contracts (`OperationalAnalyticsDto` and nested DTOs) mirroring `DashboardSummaryDto`'s `From(view)` convention. |
| T-7 | DI registration in `DependencyInjection.cs` (scoped, alongside `IAlertQueryService` / `IDashboardSummaryService`). |
| T-8 | Unit tests: `AnalyticsWindowResolverTests`, `AnalyticsCsvWriterTests`, `AnalyticsControllerTests`. |
| T-9 | Integration tests: `AnalyticsApiTests` (auth, defaults, validation, filters, grouping, latency, CSV) + `AnalyticsApiFactory`. |

### Frontend

| Task | Description |
|---|---|
| T-10 | `analytics/analytics.routes.ts` — `ANALYTICS_ROUTE`, query-param name constants. |
| T-11 | `analytics/analytics.models.ts` — wire contract transcribed field-for-field from the Backend DTOs. |
| T-12 | `analytics/analytics.service.ts` — one `getOperational(filter)` (envelope unwrap, 404 → `null`) + `exportCsvUrl`/`downloadCsv`. |
| T-13 | `shared/charts/analytics-area-chart.ts`, `analytics-bar-chart.ts`, `analytics-donut-chart.ts` — inline-SVG, `OnPush`, empty-state aware. |
| T-14 | `analytics/operational-analytics.ts` — the page: Stitch-derived layout, shared filter state in the URL, loading/empty/error per card, print-report action. |
| T-15 | `shared/shell.ts` — add the Analytics nav item between Monitoring and Branches; extend `headerTitle()`. |
| T-16 | `app.routes.ts` — `/analytics` as a `ShellComponent` child. |
| T-17 | Specs: `analytics.service.spec.ts`, `operational-analytics.spec.ts`, `analytics-charts.spec.ts`, plus shell/route additions in `shell.spec.ts`. |

### Documentation / Validation

| Task | Description |
|---|---|
| T-18 | `frontend/src/app/analytics/README.md` + updates to `docs/architecture/software-architecture-document.md` (§14.1 API table) and `postman/`. |
| T-19 | Full Backend suite + full Karma suite + Angular production build. |
| T-20 | Rebuild and redeploy **only** `backend` and `frontend` containers; volumes, MediaMTX, and SQL untouched; never `down -v`. |
| T-21 | Production validation: default view, Branch filter, detection-type filters, every date range, CSV download, empty state, error state, responsive widths. |
| T-22 | Query-plan / index review against production data; add a migration **only** if measurement justifies it, otherwise record "No database migration required." |
| T-23 | Data-correctness proof: dashboard totals, one Branch bar, one time bucket, and the latency mean each verified against independent `sqlcmd` queries. |

---

## 4. Rollback

The feature is additive and read-only. Rollback is redeploying the previous `backend`/`frontend` images; no data
migration to reverse, no volume to restore, no Jetson-side change to undo.

---

## 5. Status

**Delivered.** All tasks T-1 – T-23 complete; Backend and Frontend containers rebuilt and redeployed
on 2026-08-17. Evidence: `docs/validation/FS-15-IP-17-production-validation-report.md`.

**No database migration was required** — confirmed twice: `dotnet ef migrations
has-pending-model-changes` reports *"No changes have been made to the model since the last
migration"*, and the aggregation query measured 0 ms CPU / 0 ms elapsed against the production
`Alerts` table using the existing indexes, so T-22 proposed none.
