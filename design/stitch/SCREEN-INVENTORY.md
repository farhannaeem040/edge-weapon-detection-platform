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
| 2 | **Operations Overview** | `c8aaaacf06774149ac7ce63350496b81` | KPI/analytics dashboard | Stat tiles, alert-trend chart, site status, recent-alerts table, system-health meters | `/dashboard` (`DashboardComponent`) | ⚠️ Shell only | ⚠️ **Partial** — shell/nav only | Every KPI (active cameras, edge devices, alerts today, open incidents, avg response), the charts, site status, recent alerts and system-health panels have **no backing data**. Current `DashboardComponent` is a thin shell (title, Sign-out, "Branches" link). Style the shell; **defer** all analytics widgets. |
| 3 | **Branch Details** | `cb138afbbcc24a41b1ba4cc4c1c6b2ea` | One branch in full | Branch info card, device info, activation-key card, connected-cameras table, actions | `/branches/:branchId` (`BranchDetailComponent`) | ✅ Yes | ⚠️ **Yes, with removals** | Extra/unsupported fields: **Site Manager, Contact Phone, Timezone** (API has a single `contactDetails` string); **Primary Gateway ID "GW-LON-N72"** (never expose internal IDs; app shows `deviceId` only once *Activated*); **Last Heartbeat, System Latency chart** (no data — Increment B/future); camera **IP Address, Resolution, LIVE/OFFLINE** (API has `enabled` bool + device `activationStatus`, no per-camera live/IP/resolution); **branch "Active" pill** (no branch active/inactive concept). **Masked Activation Key shown persistently** ⛔ conflicts with FS-02 §5.4/§11 — the key is shown once at create/regenerate and never re-fetched. "Regenerate Key" **does** exist. |
| 4 | **Branch Configuration Form** | `a5f25e58f2794fdc8949f854818f5440` | Create / edit a branch + cameras | Branch-identity fields, camera-configuration repeater, Save/Cancel, validation & error banner | `/branches/new` (`BranchCreateComponent`), `/branches/:branchId/edit` (`BranchEditComponent`) | ✅ Yes | ✅ **Yes** (styling) | Splits contact into **Primary Contact Name / Email Address / Phone Number** — API has one `contactDetails` field. "4 Active Slots" is decorative. Otherwise maps cleanly to the create/edit reactive forms + camera `FormArray`. Keep the one-time key disclosure on create. |
| 5 | **Camera Management** | `bc83e0c5850b4cfca57eb18220086670` | Global camera registry + live preview | Stat tiles, camera registry table, live-preview panel, diagnostics | *(none)* | ❌ No | ❌ **No** | No global camera registry, live preview, per-camera online/offline, IP, resolution, frame rate, bitrate, encoding, "detection" toggle, diagnostics, or incident history exist. Cameras exist only **within a branch** (name + RTSP + enabled). **Defer entirely.** |
| 6 | **Edge Devices** | `6a1e91761dcd45a1bd06d66f6caa2486` | Device fleet management | Fleet stats, device table (GPU util, temp, storage, heartbeat), provision, geo map | *(none)* | ❌ No | ❌ **No** | No device fleet, GPU/temp/storage/heartbeat telemetry, "Provision Device", or geographic distribution exists. The model is single-device-per-branch, surfaced only as `activationStatus` (+ `deviceId` once activated). **Defer entirely.** |
| 7 | **Live Monitoring** | `69ad4500644d4198ad2a5590e21dcd8e` | Live camera wall + detections | Camera-feed grid, weapon-detected alert cards, confirm/dismiss/escalate | *(none)* | ❌ No | ❌ **No** | No video streaming/WebRTC, live detections, or alert triage exists (explicitly out of scope for IP-01/IP-03). **Defer — future feature.** |
| 8 | **Alerts Management** | `dbc6e4b0c6d643d7a969c37f0e8dc7ea` | Alert queue & triage | Filter bar, alert table (confidence, severity, status, assignee), bulk actions, export | *(none)* | ❌ No | ❌ **No** | No Alert entity, detections, severity, assignment, or export exists (FS-03 §8 explicitly records Alerts as future). **Defer.** |
| 9 | **Alert Review** | `15ad49fc76e04c458a4133d62eb7b3d7` | Single-alert investigation | Recording player, event timeline, audit trail, confirm/false-positive/escalate, print/share | *(none)* | ❌ No | ❌ **No** | No alerts, recordings, audit trail, or escalation exists. **Defer.** |
| 10 | **System Health** | `49a5340b737a4c49be59b071ba8795f7` | Infrastructure monitoring | Service uptime/latency/error tables, cluster utilisation, warnings log | *(none)* | ❌ No | ❌ **No** | No health metrics, cluster telemetry, or system logs surface exists. **Defer.** |
| 11 | **Operational Analytics** | `03a107b0f10b4058adaf3ffcc4a2e3f7` | Reporting & analytics | Detection-over-time chart, accuracy donut, alert density, response-time, export/report | *(none)* | ❌ No | ❌ **No** | No analytics/reporting pipeline or data exists (reporting is excluded from IP-01). **Defer.** |
| 12 | **Settings** | `86b0c8be262d423b81b36b8c706b19b0` | Detection-rule & platform config | Config nav, detection toggles, confidence slider, severity mapping, per-camera overrides | *(none)* | ❌ No | ❌ **No** | No detection rules, model config, severity mapping, notifications, integrations, or security-settings surface exists. **Defer.** A future *branch-scoped* settings view is out of current scope. |
| 13 | **Sentinel AI Logo** | `f5d11692bd8c4e7d949ea58960cd16df` | Brand asset (not a screen) | Logo mark | *(n/a — asset)* | n/a | ✅ Asset only | Not a functional screen. May inform the brand mark used in the login panel / sidebar header. No behaviour. |

---

## Implementable now (styling of real surfaces)

Only these Stitch screens correspond to features that exist in the delivered app and can receive
Stitch styling **without inventing data or navigation**:

1. **Sign-in (1)** → `/login` — drop SSO / remember-me / forgot-password / email semantics.
2. **Operations Overview (2)** → `/dashboard` — **shell, sidebar, and top header only**; no analytics
   widgets (no backing data).
3. **Branch Details (3)** → `/branches/:branchId` — style the real fields (name, address,
   contactDetails, device `activationStatus` + `deviceId`-once-activated, cameras name/rtsp/enabled,
   Edit/Delete, Regenerate). **Remove** every unsupported field and never show a persisted key.
4. **Branch Configuration Form (4)** → `/branches/new` and `/branches/:branchId/edit` — style the
   reactive form + camera `FormArray`; keep single `contactDetails`; keep one-time key disclosure on
   create.

The sidebar navigation should link **only** to the routes that exist today: Dashboard and Branches.
Every other nav item in the Stitch sidebar (Live monitoring, Alerts, Incidents, Cameras, Edge
devices, Analytics, System health, Users and access, Settings) points at a non-existent feature and
must **not** be added as live navigation.

---

## Deferred (future work — do not implement fake versions)

Screens 5–12 (Camera Management, Edge Devices, Live Monitoring, Alerts Management, Alert Review,
System Health, Operational Analytics, Settings) represent functionality with **no backing entity,
endpoint, or data** in the current milestone. They are recorded here as future work. Per FS-03 §8 and
the IP-01 exclusions (no alerts, events, reports, health records, DeepStream, WebRTC, siren,
reporting), these must not be built as placeholder/fake UIs.

---

## Cross-cutting security constraints (apply to every styled screen)

- Never render an Activation Key except the single create/regenerate disclosure; never re-fetch one.
- Never expose device shared secrets, JWTs, passwords, `DeviceRecordId`, activation-key `keyId`/hash.
- Never derive activation state from the presence of `deviceId`; use `activationStatus` only.
- RTSP URLs arrive already sanitised by the backend; render as opaque strings, never re-parse.
- Error/loading states stay generic — no backend error text, status codes, or echoed field values.
