# Feature Specification: Operational Analytics Dashboard

| Field | Value |
|-------|-------|
| Feature ID | FS-15 |
| Title | Operational Analytics Dashboard — an Admin-only, read-only analytics view over the Alerts the platform has already persisted, with Branch / detection-type / date-range filters, four charts, and a CSV export |
| Status | Delivered — deployed and validated against production 2026-08-17 (see `docs/validation/FS-15-IP-17-production-validation-report.md`) |
| Related SRS Requirements | No new SRS requirement. This feature adds a read-only analytical projection over data FS-06 (`Alert`) and FS-09 (`BranchDailyAlertQuota`, `SuppressedDetectionEvent`) already persist. |
| Related Architecture Sections | §14.1 API table (two new Backend endpoints); §13.1 domain model (read-only — no entity is added, changed, or migrated) |
| Related ADRs | None new. ADR-009 (uniform response envelope) and ADR-012 (unique-index-as-authority) are honoured unchanged. |
| Owner | Farhan Naeem |
| Dependencies | FS-06/IP-08 (detection-event backend sync — delivered): the `Alert` table and its `DetectedAtUtc`/`ReceivedAtUtc`/`ClassName`/`Confidence`/`CameraId` columns. FS-03/IP-03 (branch-camera-management — delivered): `Branch`, `Camera.BranchId`. FS-09/IP-11 (branch daily alert quota — delivered): `SuppressedDetectionEvent`. FS-10/IP-12 (monitoring-dashboard-alert-ui — delivered): the Admin shell, the `AlertController` `gun`/`knife` class whitelist, the Angular design tokens. |
| Explicitly excluded | Jetson / DeepStream / YOLO26 / NvDCF / Agent / Bridge / MediaMTX changes. Any database migration. Any write to `Alert`, `BranchDailyAlertQuota`, or `SuppressedDetectionEvent`. An operator alert-review (confirm / false-positive) workflow. A server-side PDF subsystem. Seeded or synthetic analytics data of any kind. |

---

## 1. Purpose and Scope

The platform persists every accepted detection as an `Alert` row and every quota-suppressed detection as a
`SuppressedDetectionEvent` row, but the only aggregate view of that data is the single-Branch, single-day
Dashboard summary (FS-10 §9.1). There is no way for an Admin to answer *"how has detection volume moved over the
last month?"*, *"which Branch produces the most alerts?"*, or *"how long does a detection take to reach the
Backend?"* without reading the database directly.

This feature adds an **Operational Analytics** page: one Admin-only route, backed by one aggregation endpoint and
one CSV-export endpoint, rendering four charts and a status footer over a shared filter state (date range, Branch,
detection type).

```text
Admin opens /analytics
    ↓
Angular resolves filter state from the URL query string (defaults: last 30 days, all Branches, all detections)
    ↓
GET /api/v1/analytics/operational?range=…&branchId=…&detectionType=…   (NEW)
    ↓
Backend authenticates Admin (existing ActiveAdminSessionRequirement fallback policy)
    ↓
Backend resolves the absolute UTC window and the bucket size from the preset
    ↓
Backend runs read-only SQL aggregations over Alerts ⋈ Cameras ⋈ Branches
    ↓
Backend returns summary + three series, all derived, none stored
    ↓
Angular renders four dependency-free inline-SVG charts + a real status footer
    ↓
Export CSV  → GET /api/v1/analytics/operational/export (same filters, row-level Alert data)
    Generate report → print-optimised rendering of the same page (browser Print → PDF)
```

---

## 2. Primary rule: no fabricated analytics

Every number this feature displays is either

1. read from the production database, or
2. computed from database values by a formula stated in §5 of this document, or
3. rendered as an explicit empty / unavailable state.

Nothing is seeded, sampled, defaulted to a plausible-looking constant, or carried over from the Stitch mockup. The
Stitch demonstration figures (`98.4%`, `12,402`, `203`, `42 ms`, `1,240 Edge Nodes`) appear nowhere in the
implementation, and neither do its facility names (`North Data`, `HQ`, `Logistics Hub`, `Storage B`,
`R&D Center`).

---

## 3. Domain-model inspection (what actually exists)

Inspected before design. This section is normative: the metrics in §5 are constrained by it.

### 3.1 Entities present in SQL Server

| Entity | Relevant fields |
|---|---|
| `Alert` | `AlertId`, `DeviceId` (external), `EventId`, `CameraId`, `DetectedAtUtc`, `ReceivedAtUtc`, `ClassId`, `ClassName`, `Confidence`, `FrameNumber`, `Frame{Width,Height}`, `Bbox{Left,Top,Width,Height}`, `SnapshotReference`, `SnapshotSha256`, `SnapshotContentType`, `SnapshotSizeBytes`, `SnapshotReceivedAtUtc`, `Status` |
| `SuppressedDetectionEvent` | `DeviceId`, `EventId`, `BranchId`, `LocalDate`, `ClassName`, `DetectedAtUtc`, `Reason`, `CreatedAtUtc` |
| `BranchDailyAlertQuota` | `BranchId`, `LocalDate`, `AcceptedAlertCount`, `SuppressedDetectionCount`, `GunSuppressedCount`, `KnifeSuppressedCount` |
| `Branch` | `BranchId`, `Name`, `Address`, `ContactDetails`, `TimeZoneId` |
| `Camera` | `CameraId`, `BranchId`, `Name`, `RtspUrl`, `Enabled`, `CameraKey`, `SourceOrder` |
| `Device` | `DeviceRecordId`, `DeviceId`, `BranchId`, … |

### 3.2 There is no `DetectionEvent` table on the Backend

`DetectionEvent` is the **Agent-side** (Jetson SQLite outbox) concept. Once synced (FS-06), an accepted detection
event *is* an `Alert` row; a quota-rejected one *is* a `SuppressedDetectionEvent` row. Therefore "detections" in
this feature means `Alert` rows, and the relationship is stated explicitly wherever it is displayed.

### 3.3 Timestamps that exist

| Timestamp | Exists | Meaning |
|---|---|---|
| `Alert.DetectedAtUtc` | ✅ | The Jetson's own detection time, preserved exactly as submitted (FR-SYN-004); never overwritten. |
| `Alert.ReceivedAtUtc` | ✅ | The Backend's `UtcNow` at the moment the Alert row was persisted. |
| `Alert.SnapshotReceivedAtUtc` | ✅ (nullable) | When snapshot evidence was attached (FS-08). |
| `SuppressedDetectionEvent.CreatedAtUtc` | ✅ | When the suppression record was written. |
| `AcknowledgedAtUtc` / `ResolvedAtUtc` / `UpdatedAtUtc` | ❌ | **Do not exist anywhere in the schema.** |

### 3.4 There is no human-validation workflow

`AlertStatus` is a single-member enum — `New`. `Alert.cs` states the constraint directly: *"this increment defines
none, so `Status` has no public mutator at all yet (FS-06 §5.3: only `New` is ever assigned)"*. Confirmed against
production: **594 / 594 Alerts have `Status = 'New'`.**

Consequences, which are binding on §5:

- There is **no** `Confirmed` state, **no** `FalsePositive` state, **no** `Acknowledged` state, **no** `Resolved` state.
- There is therefore **no ground truth** in the database, and consequently **no accuracy, precision, recall,
  confirmation rate, or false-positive rate can be computed.** The Stitch "Detection Accuracy — 98.4% Precision"
  donut **cannot** be implemented as designed, and is replaced per §5.2.
- There is **no operator-response timestamp**, so "Average Response Time" as designed by Stitch **cannot** be
  implemented, and is replaced per §5.4.

### 3.5 Detection taxonomy

`ClassName` is a free-text column, but the platform's own whitelist (`AlertController.KnownClassNames`, mirroring
`AlertSyncService`'s gun/knife suppression counters, FS-09 §7) is exactly `{ gun, knife }`. Production currently
holds `gun × 594`, `knife × 0`. This feature reuses that same whitelist rather than declaring a second one.

---

## 4. Filter semantics

All four cards are always driven by **one** shared filter state. There is never a per-card window.

### 4.1 Date range

| Preset | Window (resolved server-side, UTC) | Bucket |
|---|---|---|
| `last24h` | `[now − 24 h, now)` | hour |
| `last7d` | `[now − 7 d, now)` | day |
| `last30d` (**default**) | `[now − 30 d, now)` | day |
| `last90d` | `[now − 90 d, now)` | week (ISO, Monday-anchored) |

- The window is resolved from the server's `TimeProvider` (injected, so it is deterministic under test), never
  from a client-supplied clock.
- The lower bound is **inclusive**, the upper bound **exclusive** (`DetectedAtUtc >= fromUtc && < toUtc`) —
  identical to `AlertQueryService`'s existing convention, so an Alert can never be double-counted across two
  adjacent buckets or two adjacent windows.
- Buckets are UTC-anchored. A "day" bucket is a UTC calendar day. Branch-local timezones are deliberately **not**
  applied: `Branch.TimeZoneId` is null for every Branch in production, and the all-Branches view would otherwise
  have to mix incompatible calendars in one series. Documented as a known limitation (§8).
- An unrecognised preset is a `400 VALIDATION_ERROR`, never a silent fallback to the default.
- Explicit absolute `fromUtc`/`toUtc` are also accepted (used by nothing in the UI in this increment, but needed by
  the export's test surface). They are validated: `fromUtc < toUtc`, and `toUtc − fromUtc <= 366 days`. A range
  outside those bounds is a `400`.

### 4.2 Branch

- Default `All Branches` — no Branch predicate applied.
- A specific Branch is selected **by `Branch.BranchId` (GUID)**, never by display name. The wire parameter is
  `branchId`; the display value is `Branch.Name`, resolved server-side.
- A Branch is joined **authoritatively through `Camera.BranchId`** (`Alert.CameraId → Camera.CameraId →
  Camera.BranchId → Branch.BranchId`), the same path `AlertQueryService` already uses. `Alert` carries no
  `BranchId` of its own and none is inferred from `Alert.DeviceId`.
- A `branchId` that resolves to no Branch is a `404 NOT_FOUND` — not an empty chart, so "this Branch was deleted"
  is never indistinguishable from "this Branch had no alerts".

### 4.3 Detection type

- Default `All Detections` — no class predicate applied.
- Accepted values: `gun`, `knife` (case-insensitive), matching §3.5.
- Any other value is a `400 VALIDATION_ERROR`. No class outside the platform's own taxonomy is offered or accepted.

---

## 5. Metric definitions (normative)

Let **W** be the resolved window, **B** the optional Branch predicate, **C** the optional class predicate.

The **qualifying set** `Q` is:

```text
Q = { a ∈ Alerts
      | a.DetectedAtUtc >= W.fromUtc
      ∧ a.DetectedAtUtc <  W.toUtc
      ∧ (B is null ∨ camera(a).BranchId = B)
      ∧ (C is null ∨ a.ClassName = C) }
```

`Q` is computed once and every metric below is derived from it, so no two cards can ever disagree about what is
being counted.

### 5.1 Detections Over Time

- **Counts:** `|Q|`, bucketed by `DetectedAtUtc` into the §4.1 bucket size.
- **One detection = one `Alert` row**, i.e. one detection event that the Agent successfully delivered and the
  Backend accepted and persisted (FS-06). Because `Alerts` carries a unique index on `(DeviceId, EventId)`, an
  Agent retry cannot inflate this count.
- **Quota-suppressed detections are excluded.** They are not Alerts; they were never delivered to an operator, and
  including them would make the series stop meaning "alerts raised". They are surfaced separately in the summary as
  `suppressedDetections`, with the exclusion stated in the card's own subtitle — not hidden.
- **Buckets with zero Alerts are emitted with `count: 0`**, not omitted, so the x-axis is continuous and a quiet
  period is visibly quiet rather than compressed away.
- Grouped by `DetectedAtUtc` (when the weapon was seen), **not** `ReceivedAtUtc` (when the row was written) — a
  store-and-forward backlog replay must not appear as a detection spike on the day it was uploaded.

### 5.2 Detection Confidence — replacing Stitch's "Detection Accuracy"

Per §3.4 the database contains no ground truth, so the Stitch donut's `confirmed / (confirmed + false_positive)`
is **not computable and is not implemented.** No percentage is fabricated in its place.

The card is replaced by a metric the data does support, in the same donut form and the same grid slot:

- **Title:** *Detection Confidence*
- **Subtitle:** *Model confidence distribution — not human-validated*
- **Centre value:** the arithmetic mean of `Confidence` over `Q`, `0–1`, displayed as a percentage to one decimal.
- **Ring segments / legend:** counts of `Q` in three bands — **High ≥ 0.75**, **Medium 0.50 ≤ c < 0.75**,
  **Low < 0.50**. The band thresholds are display-layer bucketing of a stored value, stated on screen.
- **A permanent on-card note** records that this is the detector's own confidence and **not** an accuracy,
  precision, or false-positive measurement, and that no operator-validation workflow exists yet.
- When `|Q| = 0` the centre renders `—` and the note reads *No detections in the selected range* — never `0%`.

`Confidence` is genuinely present and genuinely varies (`Alert.Confidence`, bounded `0.0–1.0` by the domain
constructor), so this is a real measurement of a real system property. It is deliberately **not** labelled with any
word that implies ground truth.

### 5.3 Alert Density by Branch

- For each Branch that owns at least one Camera, the count of `Q` attributable to that Branch via
  `Alert.CameraId → Camera.BranchId`.
- Branch **names are read from the database** (`Branch.Name`).
- Sorted by count descending, then name ascending for a stable tie-break.
- Branches with zero qualifying Alerts **are included with `count: 0`** when the Branch filter is `All Branches`,
  so "this Branch is quiet" is distinguishable from "this Branch does not exist". When a specific Branch is
  selected, only that Branch appears.
- Renders correctly for 0 Branches, 1 Branch, and many Branches (§7).

### 5.4 Alert Delivery Latency — replacing Stitch's "Avg. Response Time"

Per §3.4 there is **no operator-response timestamp**, so *"Detection to operator validation"* is not computable.
Labelling delivery latency as response time would be a false claim about what the system measures, so the card is
retitled rather than repurposed:

- **Title:** *Alert Delivery Latency*
- **Subtitle:** *Detection on the Jetson → receipt at the Backend*

For an Alert `a`, the **delivery latency** is

```text
latency(a) = a.ReceivedAtUtc − a.DetectedAtUtc      (milliseconds)
```

**Sample-eligibility rule (normative).** `a` contributes a latency sample only when

```text
a.ReceivedAtUtc >= a.DetectedAtUtc
∧ a.ReceivedAtUtc <= DATEADD(day, 1, a.DetectedAtUtc)
```

Three reasons, all of which are stated on the card and in §8:

1. A negative latency can only be clock skew between the Jetson and the Backend, never a real delivery.
2. The Agent is a **store-and-forward** outbox: after an outage it replays a backlog, and those rows measure the
   *outage duration*, not delivery performance. Production contains exactly one such row, at **3.19 days**, which
   alone drags the naive mean from ~2.5 s to ~467 s — a 187× distortion from a single event out of 594.
3. It keeps SQL Server's `DATEDIFF(millisecond, …)` inside `int` range (it overflows past ≈24.8 days).

**Nothing is silently dropped.** The response carries `latencySampleCount` and `latencySamplesExcluded`, and the
card displays the excluded count whenever it is non-zero.

Reported values:

- `averageDeliveryLatencyMs` — mean over eligible samples in `Q`.
- `medianDeliveryLatencyMs` — the lower median (element at index `⌊(n−1)/2⌋` of the ascending latency ordering).
  Reported alongside the mean because the distribution is right-skewed even after the eligibility rule.
- `maxDeliveryLatencyMs` — the largest eligible sample.
- `latencyOverTime[]` — the mean of eligible samples per §4.1 bucket. A bucket with no eligible sample reports
  `averageMs: null` and is rendered as a gap in the line, never as `0`.

**Units:** the card renders milliseconds below 1000 ms, and seconds to one decimal at or above 1000 ms. A
millisecond value is never printed with a seconds label, or vice versa.

### 5.5 Summary block

| Field | Definition |
|---|---|
| `totalDetections` | `|Q|` |
| `suppressedDetections` | count of `SuppressedDetectionEvent` in `W`, filtered by `B` (`SuppressedDetectionEvent.BranchId`) and `C` (`ClassName`), bucketed on `DetectedAtUtc`. Reported for context, **never** added to `totalDetections`. |
| `meanConfidence` | mean `Confidence` over `Q`; `null` when `|Q| = 0` |
| `confidenceHigh/Medium/Low` | §5.2 band counts |
| `averageDeliveryLatencyMs` / `medianDeliveryLatencyMs` / `maxDeliveryLatencyMs` | §5.4; `null` when there is no eligible sample |
| `latencySampleCount` / `latencySamplesExcluded` | §5.4 |
| `alertsWithSnapshot` | count of `Q` with `SnapshotReference != null` — evidence coverage (FS-08) |
| `branchCount` / `cameraCount` / `deviceCount` | fleet counts in scope of `B` |
| `generatedAtUtc` | server `TimeProvider` value at response time |
| `validationDataAvailable` | **always `false` in this increment** — an explicit, machine-readable statement that no confirm / false-positive ground truth exists, so the UI never has to infer it |

### 5.6 Status footer

Rendered only from fields above: `generatedAtUtc`, `cameraCount`, `deviceCount`, `branchCount`,
`medianDeliveryLatencyMs`, and the client's own last-refresh time. No edge-node count, no engine-sync state, and no
processing-latency figure is displayed, because the platform exposes none of those. If the response is absent
(error state), the footer is omitted rather than shown with placeholders.

---

## 6. API surface

Both endpoints are `[ApiController]`s with **no `[AllowAnonymous]`**, so the application's default/fallback
`ActiveAdminSessionRequirement` policy applies exactly as it does to `AlertController`, `BranchController`,
`DashboardController`, and `LiveMonitoringController`. An unauthenticated request, an expired token, or Device
credentials (`X-Device-Id`/`X-Device-Secret`) all yield `401` before the action body runs.

### 6.1 `GET /api/v1/analytics/operational`

Query parameters: `range` (§4.1 preset), or `fromUtc` + `toUtc`; `branchId` (GUID); `detectionType` (`gun`|`knife`).

Response `data` (wrapped in the standard envelope by `ApiEnvelopeResultFilter`):

```jsonc
{
  "filters": { "range": "last30d", "fromUtc": "…", "toUtc": "…", "bucket": "day",
               "branchId": null, "branchName": null, "detectionType": null },
  "summary": { "totalDetections": 0, "suppressedDetections": 0, "meanConfidence": null,
               "confidenceHigh": 0, "confidenceMedium": 0, "confidenceLow": 0,
               "averageDeliveryLatencyMs": null, "medianDeliveryLatencyMs": null,
               "maxDeliveryLatencyMs": null, "latencySampleCount": 0, "latencySamplesExcluded": 0,
               "alertsWithSnapshot": 0, "branchCount": 0, "cameraCount": 0, "deviceCount": 0,
               "validationDataAvailable": false, "generatedAtUtc": "…" },
  "detectionsOverTime": [ { "periodStartUtc": "…", "label": "…", "count": 0 } ],
  "detectionsByBranch":  [ { "branchId": "…", "branchName": "…", "count": 0 } ],
  "latencyOverTime":     [ { "periodStartUtc": "…", "label": "…", "averageMs": null, "sampleCount": 0 } ]
}
```

### 6.2 `GET /api/v1/analytics/operational/export`

Same filter parameters, same validation, same authorization. Returns `text/csv; charset=utf-8` with
`Content-Disposition: attachment; filename="operational-analytics-<yyyy-MM-dd>.csv"`.

Columns — **exactly** these, in this order:

```text
Detected At (UTC), Received At (UTC), Delivery Latency (ms), Branch, Camera, Detection Type, Confidence, Status, Snapshot Available
```

- `Delivery Latency (ms)` is blank for a row excluded by §5.4's eligibility rule, never a misleading number.
- **Nothing sensitive is exported:** no `Camera.RtspUrl`, no `CameraKey`, no `Device` secret or activation key, no
  `SnapshotReference`, no filesystem path, no `DeviceRecordId`. Only `SnapshotAvailable` (a boolean) reveals
  anything about snapshots — the same posture `AlertListItemView` already takes.
- RFC 4180 quoting: a value containing `"`, `,`, CR, or LF is double-quoted with embedded quotes doubled. A value
  beginning with `=`, `+`, `-`, or `@` is prefixed with a single quote to defuse spreadsheet formula injection.
- Bounded: at most 50,000 rows, ordered by `DetectedAtUtc` ascending. If the filter matches more, the export is a
  `400` telling the Admin to narrow the range — never a silently truncated file.

---

## 7. UI requirements

- New sidebar item **Analytics**, placed after **Monitoring** and before **Branches**, using the existing icon
  style (inline 24×24 stroke SVG), active state, hover state, and off-canvas behaviour. New route `/analytics`
  under the existing authenticated `ShellComponent`, guarded by the existing `authGuard`.
- LJMU branding is preserved throughout. Nothing from the Stitch shell is adopted: no "Sentinel AI", no
  "Alex Rivers", no "Chief Security Officer", no avatar, no search bar, no notification bell, no locale switcher,
  no Stitch sidebar entries.
- Page header: breadcrumb `Dashboard / Analytics`, title **Operational Analytics**, subtitle line
  *Performance metrics — analyse security events and delivery performance across Branches.*
- Actions: **Export CSV** (secondary) and **Generate report** (primary). Both have real behaviour; neither is a
  dead control.
- Filter bar reproduces the Stitch filter-card: a bordered surface card holding three labelled selects
  (Date range / Branch / Detection type) plus a right-aligned refresh control.
- Grid follows Stitch's bento layout: 8/4 on the first row (large area chart + donut), 6/6 on the second
  (bar chart + line chart), footer spanning the full width. Below 1024 px every card stacks to full width.
- **Charts are dependency-free inline SVG components.** The project has no charting library and, deliberately, no
  UI framework at all (`package.json` carries only Angular + rxjs + tslib + zone.js). Four small charts do not
  justify importing a visualisation framework; see IP-17 §1.
- Every card independently supports **loading**, **data**, **no data for the selected filters**, and **error**.
  A missing metric renders `—` with an explanation; `0%`, `0 ms`, or an empty chart area is never used to stand in
  for absent data.
- **Generate report** switches the page into a print-optimised presentation (charts and values retained, chrome and
  interactive controls suppressed) and invokes the browser's print dialog, producing a PDF via Print → Save as PDF.
  No server-side PDF subsystem is introduced; this is recorded as a deliberate scope decision, not an omission.

---

## 8. Known limitations (stated, not worked around)

1. **No accuracy / precision metric is possible.** `AlertStatus` has one member. Until an operator-review workflow
   exists (a future feature), the platform has no ground truth and this dashboard says so explicitly rather than
   presenting a number.
2. **No operator-response time is possible.** No acknowledgement or resolution timestamp exists. Delivery latency
   is measured and labelled as delivery latency.
3. **Buckets are UTC**, not Branch-local. `Branch.TimeZoneId` is null for every production Branch; an all-Branches
   series cannot mix calendars coherently.
4. **Latency excludes backlog replays** by the §5.4 rule, with the excluded count always reported.
5. **`knife` has no production data yet** (594 gun, 0 knife). The knife filter is implemented and tested, and
   correctly renders an empty state against current data.
6. **Suppressed detections are counted by `SuppressedDetectionEvent.BranchId`**, which is a stored column, whereas
   Alerts are attributed via `Camera.BranchId`. Both are authoritative for their own row type; they are never
   summed together.

---

## 9. Acceptance criteria

| # | Criterion |
|---|---|
| AC-1 | `Analytics` appears in the authenticated sidebar between Monitoring and Branches and routes to `/analytics`. |
| AC-2 | `/analytics` is unreachable without a session (client guard) and both endpoints answer `401` without a valid Admin token (server). |
| AC-3 | Default state is Last 30 days / All Branches / All detections, and all four cards use that one window. |
| AC-4 | `totalDetections` equals an independent `SELECT COUNT(*)` over `Alerts` for the same window and filters. |
| AC-5 | At least one Branch bar equals an independent per-Branch SQL count. |
| AC-6 | At least one time bucket equals an independent SQL count for that bucket's `[start, end)`. |
| AC-7 | `averageDeliveryLatencyMs` equals an independent SQL mean over the §5.4-eligible rows. |
| AC-8 | No confirmed / false-positive figure is displayed anywhere; the confidence card carries its non-validation note. |
| AC-9 | CSV downloads with the documented filename, respects all three filters, is RFC 4180-valid, and contains no RTSP URL, credential, snapshot reference, or filesystem path. |
| AC-10 | Loading, empty, and error states each render distinctly on every card. |
| AC-11 | The layout stacks without horizontal overflow at 1280 px, 1024 px, 900 px, and 375 px. |
| AC-12 | No database migration is produced; `dotnet ef migrations has-pending-model-changes` reports none. |
| AC-13 | Dashboard, Alerts, Alert detail, snapshot evidence, Monitoring, Branches, and authentication are unchanged and green. |
| AC-14 | No Jetson, DeepStream, Agent, Bridge, or MediaMTX file is modified. |
