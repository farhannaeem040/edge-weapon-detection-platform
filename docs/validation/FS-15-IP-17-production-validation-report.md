# FS-15 / IP-17 — Operational Analytics — Production Validation Report

| Field | Value |
|---|---|
| Feature | FS-15 — Operational Analytics Dashboard |
| Plan | IP-17 — Operational Analytics Dashboard |
| Date | 2026-08-17 |
| Environment | Production Docker stack (`weapon-detection`), single Branch ("Ljmu Branch"), 2 Cameras, 1 Jetson Device |
| Result | **PASS** |

---

## 1. Production baseline (recorded before deployment)

| Item | Value |
|---|---|
| Backend image | `weapon-detection-backend:latest` — `bdcc7788d65c` |
| Frontend image | `weapon-detection-frontend:latest` — `c9de375f0c6e` |
| SQL Server | `mcr.microsoft.com/mssql/server:2022-CU21-ubuntu-22.04` — healthy |
| MediaMTX | digest-pinned `f37aaaf1a707` — running |
| Backend health | `{"success":true,"data":{"status":"Healthy"}}` |
| Alerts | 594 |
| Alerts with snapshot | 568 |
| SuppressedDetectionEvents | 2,154 |
| BranchDailyAlertQuotas | 8 |
| Branches / Cameras / Devices | 1 / 2 / 1 |
| Alerts with a status other than `New` | 0 |

No production data was created, modified, or seeded at any point to populate the dashboard.

## 2. Deployment

Only `backend` and `frontend` were rebuilt and recreated (`docker compose build backend frontend`
then `docker compose up -d --no-deps backend frontend`). `sqlserver` and `mediamtx` were left running
untouched (uptime unbroken). `docker compose down -v` was never used; all three named volumes
(`sqlserver-data`, `dataprotection-keys`, `alert-snapshots`) survived.

| Image | Before | After |
|---|---|---|
| Backend | `bdcc7788d65c` | `810750e7da54` |
| Frontend | `c9de375f0c6e` | `e95636f5fbc7` (rebuilt once more as `e95636f5fbc7` after a UI copy fix) |

All four containers healthy after deployment.

## 3. Database migration

**No database migration required.**

- `dotnet ef migrations has-pending-model-changes` → *"No changes have been made to the model since
  the last migration."*
- Every column the feature reads already exists (`Alerts.DetectedAtUtc`, `ReceivedAtUtc`,
  `ClassName`, `Confidence`, `CameraId`, `SnapshotReference`, `Status`; `Cameras.BranchId`;
  `Branches.Name`; `SuppressedDetectionEvents.*`).
- Existing indexes on `Alerts` (`PK_Alerts`, `IX_Alerts_CameraId`, `IX_Alerts_DeviceId`,
  `IX_Alerts_DeviceId_EventId`) were measured as sufficient: the daily-bucket + latency aggregation
  over the production table reported `CPU time = 0 ms, elapsed time = 0 ms`. No index was added,
  because no measurement justified one.

## 4. Data-correctness proof (dashboard vs. authoritative SQL)

Window: the exact `[fromUtc, toUtc)` the API echoed —
`2026-07-18T06:10:56.9462552Z` → `2026-08-17T06:10:56.9462552Z`, all Branches, all detection types.

| Metric | Dashboard / API | Independent `sqlcmd` query | Match |
|---|---|---|---|
| Total detections | 594 | 594 | ✅ |
| Alert density — "Ljmu Branch" | 594 | 594 | ✅ |
| Time bucket `2026-08-13` (daily) | 415 | 415 | ✅ |
| Mean confidence | 0.7579571759259259 | 0.7579571759259259 | ✅ |
| Confidence bands (high / medium / low) | 375 / 219 / 0 | 375 / 219 / 0 | ✅ |
| Average delivery latency | 2489.83 ms | 2489.8 ms | ✅ |
| Median delivery latency (lower median of eligible samples) | 2437 ms | 2437 ms | ✅ |
| Max delivery latency | 9389 ms | 9389 ms | ✅ |
| Latency samples / excluded | 593 / 1 | 593 / 1 | ✅ |
| Alerts with snapshot | 568 | 568 | ✅ |
| Suppressed detections | 2154 | 2154 | ✅ |

Every non-zero daily bucket the API returned (`2026-08-12` = 28, `2026-08-13` = 415,
`2026-08-17` = 151) matches the raw `GROUP BY CAST(DetectedAtUtc AS date)` exactly.

### 4.1 Why the latency eligibility rule matters (evidence)

A raw `AVG(DATEDIFF(millisecond, DetectedAtUtc, ReceivedAtUtc))` over the `2026-08-13` bucket returns
**666,704 ms**; with FS-15 §5.4's eligibility rule applied the same bucket's mean is a realistic
**2.5 s**. The distortion comes from a *single* Alert out of 594 whose delivery latency is 3.19 days —
a store-and-forward backlog replay measuring an Agent outage, not delivery performance. The rule
excludes exactly that one row, and the dashboard displays `1 detection was excluded from this
metric` rather than hiding it.

## 5. Filter validation

| Filter | Bucket | Buckets returned | Total detections | Avg latency |
|---|---|---|---|---|
| `range=last24h` | hour | 25 | 151 | 3135.6 ms |
| `range=last7d` | day | 8 | 594 | 2489.8 ms |
| `range=last30d` (default) | day | 31 | 594 | 2489.8 ms |
| `range=last90d` | week | 14 | 594 | 2489.8 ms |
| `branchId=<Ljmu Branch>` | day | 31 | 594 | 2489.8 ms |
| `detectionType=gun` | day | 31 | 594 | 2489.8 ms |
| `detectionType=knife` | day | 31 | **0** | **null** |
| `branchId` + `gun` + `last7d` | day | 8 | 594 | 2489.8 ms |

`last24h` = 151 corresponds exactly to the `2026-08-17` daily bucket, confirming the two windows
agree on the same underlying rows.

### 5.1 Error paths

| Request | Expected | Actual |
|---|---|---|
| `GET /analytics/operational` with no token | 401 | 401 ✅ |
| `GET /analytics/operational/export` with no token | 401 | 401 ✅ |
| `branchId` that resolves to no Branch | 404 | 404 ✅ |
| `detectionType=intrusion` | 400 | 400 ✅ |

## 6. CSV export proof

`GET /api/v1/analytics/operational/export?range=last24h`

| Check | Result |
|---|---|
| HTTP status | 200 ✅ |
| Content-Type | `text/csv; charset=utf-8` ✅ |
| Filename | `operational-analytics-2026-08-17.csv` ✅ |
| Header row | `Detected At (UTC),Received At (UTC),Delivery Latency (ms),Branch,Camera,Detection Type,Confidence,Status,Snapshot Available` ✅ |
| Row count | 151 data rows — exactly the `last24h` detection total ✅ |
| Filter respected | every row `Ljmu Branch` / `gun` / `New`, timestamps inside the 24-hour window ✅ |
| Knife filter | header only (0 data rows) ✅ |
| Leak scan (`rtsp://`, `.mp4`, `/var/lib`, `SharedSecret`, `activationKey`, `sha256`, Tailscale/loopback IPs, `cameras/`, `ProtectedShared`, `DeviceRecordId`) | **all absent** ✅ |

Sample row: `2026-08-17 00:22:03,2026-08-17 00:22:06,2834,Ljmu Branch,Front Camera,gun,0.8745,New,Yes`

## 7. UI validation

Captured through headless Chrome against the deployed stack, authenticated as the production Admin.

| Check | Result |
|---|---|
| `Analytics` appears in the sidebar between Monitoring and Branches | ✅ |
| `/analytics` renders inside the existing authenticated shell | ✅ |
| LJMU branding preserved; no "Sentinel AI", persona, avatar, search bar, or notification bell | ✅ |
| Stitch layout adapted: header + actions, filter card, 8/4 + 6/6 bento grid, charcoal status footer | ✅ |
| Four cards render with real values (594 detections, 75.8 % mean confidence, Ljmu Branch = 594, median 2.4 s) | ✅ |
| No mockup figure present (`98.4%`, `12,402`, `203`, `42 ms`, `1,240 Edge Nodes`, Stitch facility names) | ✅ |
| Responsive: 1440 / 1280 / 900 / 390 px — no horizontal overflow at any width, cards stack below 1024 px | ✅ |

### 7.1 Empty-state proof (`detectionType=knife`, no knife data in production)

- Confidence donut centre renders **`—`**, not `0%`; ring empty; note reads *"No detections in the
  selected range, so there is no confidence to summarise."*
- Latency card renders *"No delivery-latency samples in the selected range."*, and the chip reads
  **`Median —`**.
- Status footer reads `Median delivery: —`.
- Detections chart renders a *measured zero* baseline (those buckets genuinely contain zero Alerts),
  which is correctly distinct from the unmeasured-latency gap.

### 7.2 Error-state proof

Proven by Karma unit test rather than by inducing a production outage: with the analytics request
failing, all four cards render their error state and the status footer is omitted rather than shown
with placeholders (`operational-analytics.spec.ts`, *"shows an error state on every card when the
request fails"*). The Angular route guard normalises an unrecognised `range` in the URL, so an
invalid bookmark cannot reach the Backend from the UI at all.

## 8. Performance

| Request | Elapsed |
|---|---|
| `range=last24h` (hourly, 25 buckets) | 58 ms |
| `range=last30d` (daily, 31 buckets) | 10 ms |
| `range=last90d` (weekly, 14 buckets) | 8 ms |
| CSV export, `last30d` (594 rows) | 56 ms |

## 9. Regression

| Area | Result |
|---|---|
| Backend health | `Healthy` ✅ |
| `GET /api/v1/alerts` | 200 ✅ |
| `GET /api/v1/alerts/{id}` | 200 ✅ |
| `GET /api/v1/alerts/{id}/snapshot` | 200, 115,732-byte JPEG ✅ |
| `GET /api/v1/branches`, `/branches/{id}` | 200 ✅ |
| `GET /api/v1/dashboard/summary` | 200 ✅ |
| `GET /api/v1/branches/{id}/live-monitoring/cameras` | 200 ✅ |
| SPA routes `/`, `/dashboard`, `/alerts`, `/monitoring`, `/analytics`, `/branches` | 200 ✅ |
| SQL Server / MediaMTX | untouched, uptime unbroken ✅ |
| Post-deploy data | Alerts 594, Branches 1, Cameras 2, Devices 1, Suppressed 2154, Quotas 8, snapshots 568, non-`New` statuses **0** — identical to baseline ✅ |
| Jetson / DeepStream / YOLO26 / NvDCF / Agent / Bridge | **no files changed by this feature** ✅ |

The working tree's pre-existing, unrelated modifications under `deployment/jetson/**` and the deleted
`deployment/jetson.zip` were already present when FS-15 work began and were not touched.

## 10. Test totals

| Suite | Before | After |
|---|---|---|
| Backend unit (`WeaponDetection.UnitTests`) | 484 passed / 0 failed | **574 passed / 0 failed** |
| Backend integration (`WeaponDetection.IntegrationTests`) | 475 passed / 0 failed | **517 passed / 0 failed** |
| Frontend (Karma) | 465 passed / 0 failed | **551 passed / 0 failed** |

Angular production build succeeded. It emits one budget **warning** — initial bundle 514.16 kB against
a 500 kB warning threshold (error threshold 1 MB, not reached). This is reported rather than silenced;
the threshold was deliberately not raised.

## 11. Known limitations (carried from FS-15 §8)

1. No accuracy / precision / false-positive metric is possible — `AlertStatus` has one member, so the
   database holds no human-validated ground truth. The Stitch "98.4 % precision" donut was replaced by
   **Detection Confidence**, explicitly labelled as not human-validated.
2. No operator-response time is possible — no acknowledgement or resolution timestamp exists. The
   Stitch "Avg. Response Time" card was retitled **Alert Delivery Latency** and measures
   `ReceivedAtUtc − DetectedAtUtc`.
3. Buckets are UTC, not Branch-local (`Branch.TimeZoneId` is null for every production Branch).
4. Latency excludes clock skew and >1-day backlog replays; the excluded count is always displayed.
5. `knife` has no production data yet — implemented, tested, and correctly renders an empty state.
6. "Generate report" is a print-optimised view (Print → Save as PDF), not a server-side PDF
   subsystem. This is a deliberate scope decision recorded in IP-17 §1.9, not an omission.

---

**FS-15 / IP-17 — OPERATIONAL ANALYTICS — PASS — DEPLOYED AND VALIDATED**
