# Sentinel — Stitch Screen Inventory & Angular Mapping

> **Source:** Stitch project *Sentinel AI Security Platform* (`projects/12701037052481013848`).
> All 13 screens were inspected (12 rendered screens + 1 logo asset). This inventory maps each Stitch
> screen to the **existing** Angular application, states whether the underlying feature exists today,
> whether it is safe to implement (style) now, and every mismatch against the current project
> specification (FS-01 / FS-02 / FS-03, IP-01 / IP-03) and delivered data model.

## Legend

- **Feature exists?** — Is the backing feature/route/data present in the current app today?
- **Safe to implement now?** — May we apply Stitch styling to a *real* existing surface without
  inventing data, navigation, or exposing secrets?

---

## Summary table

| # | Stitch screen | Screen ID | Purpose | Major components | Angular route (existing) | Feature exists? | Safe now? | Spec mismatch |
|---|---------------|-----------|---------|------------------|--------------------------|-----------------|-----------|---------------|
| 1 | **Sign-in** | `ae121b75bac144c4a7d2e314ce0e7a4f` | Admin login | Split layout: brand panel + email/password form, "Sign in" CTA | `/login` (`LoginComponent`) | ✅ Yes | ✅ **Yes** (styling) | "Remember this device", "Forgot password?", "Single Sign-On (SSO)", "name@company.com" email field — **none exist**. App uses `credentialIdentifier` + password only (FS-01 §7, §11). Drop the extras. |
| 2 | **Operations Overview** | `c8aaaacf06774149ac7ce63350496b81` | KPI/analytics dashboard | Stat tiles, alert-trend chart, site status, recent-alerts table, system-health meters | `/dashboard` (`DashboardSummaryComponent`, FS-10/IP-12) | ✅ Yes (partial data) | ✅ **Yes, with removals** (FS-10) | Real, bounded data now exists via `GET /api/v1/dashboard/summary`: today's Branch-local Alert count/max/remaining, suppression totals (gun/knife), latest Alert timestamp, Device/Camera counts. **Still absent/removed**: multi-site selector ("All Global Sites"), the alert-trend bar chart (no time-series data stored), Open Incidents / Avg Response tiles (no incident concept), Site Status list (single Branch, no multi-site), System Health meters (no infra-telemetry). "Recent Critical Alerts" becomes a small bounded (5–10 row) real Alert snippet linking to `/alerts`, not a decorated table with confidence/severity styling beyond what the Alert entity actually has. |
| 3 | **Branch Details** | `cb138afbbcc24a41b1ba4cc4c1c6b2ea` | One branch in full | Branch info card, device info, activation-key card, connected-cameras table, actions | `/branches/:branchId` (`BranchDetailComponent`) | ✅ Yes | ⚠️ **Yes, with removals** | Extra/unsupported fields: **Site Manager, Contact Phone, Timezone** (API has a single `contactDetails` string); **Primary Gateway ID "GW-LON-N72"** (never expose internal IDs; app shows `deviceId` only once *Activated*); **Last Heartbeat, System Latency chart** (no data — Increment B/future); camera **IP Address, Resolution, LIVE/OFFLINE** (API has `enabled` bool + device `activationStatus`, no per-camera live/IP/resolution); **branch "Active" pill** (no branch active/inactive concept). **Masked Activation Key shown persistently** ⛔ conflicts with FS-02 §5.4/§11 — the key is shown once at create/regenerate and never re-fetched. "Regenerate Key" **does** exist. |
| 4 | **Branch Configuration Form** | `a5f25e58f2794fdc8949f854818f5440` | Create / edit a branch + cameras | Branch-identity fields, camera-configuration repeater, Save/Cancel, validation & error banner | `/branches/new` (`BranchCreateComponent`), `/branches/:branchId/edit` (`BranchEditComponent`) | ✅ Yes | ✅ **Yes** (styling) | Splits contact into **Primary Contact Name / Email Address / Phone Number** — API has one `contactDetails` field. "4 Active Slots" is decorative. Otherwise maps cleanly to the create/edit reactive forms + camera `FormArray`. Keep the one-time key disclosure on create. |
| 5 | **Camera Management** | `bc83e0c5850b4cfca57eb18220086670` | Global camera registry + live preview | Stat tiles, camera registry table, live-preview panel, diagnostics | *(none)* | ❌ No | ❌ **No** | No global camera registry, live preview, per-camera online/offline, IP, resolution, frame rate, bitrate, encoding, "detection" toggle, diagnostics, or incident history exist. Cameras exist only **within a branch** (name + RTSP + enabled). **Defer entirely.** |
| 6 | **Edge Devices** | `6a1e91761dcd45a1bd06d66f6caa2486` | Device fleet management | Fleet stats, device table (GPU util, temp, storage, heartbeat), provision, geo map | *(none)* | ❌ No | ❌ **No** | No device fleet, GPU/temp/storage/heartbeat telemetry, "Provision Device", or geographic distribution exists. The model is single-device-per-branch, surfaced only as `activationStatus` (+ `deviceId` once activated). **Defer entirely.** |
| 7 | **Live Monitoring** | `69ad4500644d4198ad2a5590e21dcd8e` | Live camera wall + detections | Camera-feed grid, weapon-detected alert cards, confirm/dismiss/escalate | *(none)* | ❌ No | ❌ **No** | No video streaming/WebRTC, live detections, or alert triage exists (explicitly out of scope for IP-01/IP-03). **Defer — future feature.** |
| 8 | **Alerts Management** | `dbc6e4b0c6d643d7a969c37f0e8dc7ea` | Alert queue & triage | Filter bar, alert table (confidence, severity, status, assignee), bulk actions, export | `/alerts` (`AlertListComponent`, FS-10/IP-12) | ✅ Yes | ✅ **Yes, with removals** (FS-10) | The Alert entity, real Backend data, and `GET /api/v1/alerts` now exist (FS-06/FS-09/FS-10). **Removed**: severity (no severity concept — only weapon class Gun/Knife), assignee/"Assign To" (no user/assignment model), bulk select + Confirm/False-Positive actions (no status-transition feature approved), Export, global search, min-confidence slider filter (not a requested filter). "Detection Type" → weapon class badge (Gun/Knife/Unknown only, never "Human Intrusion"/"Vehicle Loitering"). Filters kept: date range, class, Branch, Camera, Status, snapshot-availability — server-side, URL-persisted, newest-first default. Table columns kept: detected time, class, confidence, Branch, Camera, Device, Status, snapshot indicator, open-detail action. |
| 9 | **Alert Review** | `15ad49fc76e04c458a4133d62eb7b3d7` | Single-alert investigation | Recording player, event timeline, audit trail, confirm/false-positive/escalate, print/share | `/alerts/:alertId` (`AlertDetailComponent`, FS-10/IP-12) | ✅ Yes (partial data) | ✅ **Yes, with removals** (FS-10) | Real Alert detail now exists via `GET /api/v1/alerts/{id}`. **Removed entirely**: live recording player (no video/streaming), Event Timeline scrubber (no time-series/audit data), Audit Trail & Operator Activity log (no operator-action tracking — Alert status is never mutated by this feature), Confirm Threat/False Positive/Escalate buttons (no approved status-transition feature), comment box, Print/Share. **Kept, restyled**: the right-side metadata panel layout pattern only — detected/received timestamps, class, confidence, Branch, Camera, Device, Status (`New`, never mutated), delivery latency. The video area is replaced by a snapshot section: when `SnapshotReference` is null (every Alert in this increment), a professional placeholder card reading "Snapshot evidence is not available for this Alert" — never the mockup's live-feed HUD overlay, bounding box, or fabricated CCTV image. |
| 10 | **System Health** | `49a5340b737a4c49be59b071ba8795f7` | Infrastructure monitoring | Service uptime/latency/error tables, cluster utilisation, warnings log | *(none)* | ❌ No | ❌ **No** | No health metrics, cluster telemetry, or system logs surface exists. **Defer.** |
| 11 | **Operational Analytics** | `03a107b0f10b4058adaf3ffcc4a2e3f7` | Reporting & analytics | Detection-over-time chart, accuracy donut, alert density, response-time, export/report | *(none)* | ❌ No | ❌ **No** | No analytics/reporting pipeline or data exists (reporting is excluded from IP-01). **Defer.** |
| 12 | **Settings** | `86b0c8be262d423b81b36b8c706b19b0` | Detection-rule & platform config | Config nav, detection toggles, confidence slider, severity mapping, per-camera overrides | *(none)* | ❌ No | ❌ **No** | No detection rules, model config, severity mapping, notifications, integrations, or security-settings surface exists. **Defer.** A future *branch-scoped* settings view is out of current scope. |
| 13 | **Sentinel AI Logo** | `f5d11692bd8c4e7d949ea58960cd16df` | Brand asset (not a screen) | Logo mark | *(n/a — asset)* | n/a | ✅ Asset only | Not a functional screen. May inform the brand mark used in the login panel / sidebar header. No behaviour. |

---

## Implementable now (styling of real surfaces)

These Stitch screens correspond to features that exist in the delivered app and can receive
Stitch styling **without inventing data or navigation**:

1. **Sign-in (1)** → `/login` — drop SSO / remember-me / forgot-password / email semantics.
2. **Operations Overview (2)** → `/dashboard` — shell/sidebar/top header (done), **plus real KPI
   tiles/quota card/recent-Alerts snippet (FS-10/IP-12)**; no chart/incident/multi-site widgets (no
   backing data for those).
3. **Branch Details (3)** → `/branches/:branchId` — style the real fields (name, address,
   contactDetails, device `activationStatus` + `deviceId`-once-activated, cameras name/rtsp/enabled,
   Edit/Delete, Regenerate). **Remove** every unsupported field and never show a persisted key.
4. **Branch Configuration Form (4)** → `/branches/new` and `/branches/:branchId/edit` — style the
   reactive form + camera `FormArray`; keep single `contactDetails`; keep one-time key disclosure on
   create.
5. **Alerts Management (8)** → `/alerts` (**FS-10/IP-12**) — paginated/filtered real Alert list; see
   row 8 above for the full removal list (severity, assignee, bulk actions, export, confidence slider).
6. **Alert Review (9)** → `/alerts/:alertId` (**FS-10/IP-12**) — real Alert detail; see row 9 above for
   the full removal list (video player, timeline, audit trail, confirm/false-positive/escalate).

The sidebar navigation links **only** to routes that exist today: Dashboard, Alerts, and Branches.
Every other nav item in the Stitch sidebar (Live monitoring, Incidents, Cameras, Edge devices,
Analytics, System health, Users and access, Settings) points at a non-existent feature and must **not**
be added as live navigation.

---

## Deferred (future work — do not implement fake versions)

Screens 5–7, 10–12 (Camera Management, Edge Devices, Live Monitoring, System Health, Operational
Analytics, Settings) represent functionality with **no backing entity, endpoint, or data** in the
current milestone. They are recorded here as future work. Per FS-10 §15's exclusions (no live
video/streaming, siren, status transitions, device-fleet telemetry, analytics/reporting), these must
not be built as placeholder/fake UIs. Alerts Management and Alert Review (screens 8–9) have graduated
out of this list per FS-10/IP-12 — see the "Implementable now" list above.

---

## Cross-cutting security constraints (apply to every styled screen)

- Never render an Activation Key except the single create/regenerate disclosure; never re-fetch one.
- Never expose device shared secrets, JWTs, passwords, `DeviceRecordId`, activation-key `keyId`/hash.
- Never derive activation state from the presence of `deviceId`; use `activationStatus` only.
- RTSP URLs arrive already sanitised by the backend; render as opaque strings, never re-parse.
- Error/loading states stay generic — no backend error text, status codes, or echoed field values.
