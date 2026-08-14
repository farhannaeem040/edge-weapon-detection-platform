# Sentinel — Stitch → Angular Implementation Map

> **Purpose:** Map the *approved, implementable* Stitch components (visual spec) onto the **existing**
> Angular application so the design can be applied later as styling — without changing behaviour,
> backend contracts, or the Agent project, and without inventing data or navigation.
>
> **Scope guard:** Only the four implementable Stitch screens feed this map — Sign-in, the dashboard
> **shell**, Branch Details, and the Branch Configuration Form (see `SCREEN-INVENTORY.md`). Everything
> Stitch shows beyond the current data model is deferred, not mapped.
>
> **Nature of Stitch output:** Angular, **not** React. Stitch emits React/Tailwind HTML — treat it as
> a *visual specification only*. Do **not** copy generated code. Reproduce the look with Angular
> component templates + component-scoped CSS (or global tokens), matching the existing code's idiom
> (standalone components, signals, `ChangeDetectionStrategy.OnPush`, reactive forms).

---

## Implementation status

**Round 1** implemented on branch `feat/ui-stitch-redesign` (frontend visual redesign only; no Backend
or Agent change; all routes, contracts, validation, and security behaviour preserved).

**Round 2 (FS-10/IP-12)** adds real Dashboard-summary, Alert-list, and Alert-detail content — the first
increment with new, additive, read-only Backend endpoints (`GET /api/v1/dashboard/summary`,
`GET /api/v1/alerts`, `GET /api/v1/alerts/{id}`) behind the existing Admin-JWT policy. See §§15–18
below. Summary of what landed against Round 1 of this map:

| Item (below) | Status |
|--------------|--------|
| Design tokens (`src/styles.css`) | ✅ done — tokens + shared primitives, no framework, no font files |
| Authenticated shell (`ShellComponent`) + sidebar + top header | ✅ done — nav lists only Branches; deferred screens absent; responsive off-canvas |
| Login | ✅ done — split panel; no SSO/Remember/Forgot; `credentialIdentifier`+password kept |
| Branch list | ✅ done — cards/rows, badge, camera count, Edit/Delete, states |
| Branch detail | ✅ done — card layout, API-only fields, one-time key regeneration |
| Create / Edit | ✅ done — shared card form, single `contactDetails`, camera FormArray |
| Camera FormArray | ✅ done — restyled rows, add/remove, min-one preserved |
| Status badge / Activation Key display / Edit-Delete icons / delete dialog | ✅ done — restyled, hooks kept; dialog gains Escape + focus return |
| Loading / error / empty states | ✅ done — banners, spinner, empty-state |

**Deliberate differences from the Stitch mockups** (forced by the API contract and security rules):
the branch detail omits Site Manager / phone+email split / timezone / gateway id / heartbeat / latency
and per-camera IP / resolution / live-state (not in the contract); the Activation Key is shown only
once after create/regenerate, never as a persistent or masked card; the config form keeps a single
`contactDetails` field; the sidebar shows only Branches (deferred screens are not linked); the login
drops SSO / Remember-device / Forgot-password.

Tests: **321 passing**; production build clean within budgets; `npm audit` reports 0 vulnerabilities.

## 0. Existing structure this maps onto

Angular 20 standalone app. Modules (IP-01 §3): `core`, `auth`, `branches`, `shared`.

Routes today (`app.routes.ts`):

| Route | Component | Guard |
|-------|-----------|-------|
| `/login` | `LoginComponent` | — (public) |
| `/dashboard` | `DashboardComponent` (thin shell) | `authGuard` |
| `/branches` | `BranchListComponent` | `authGuard` |
| `/branches/new` | `BranchCreateComponent` | `authGuard` |
| `/branches/:branchId/edit` | `BranchEditComponent` | `authGuard` |
| `/branches/:branchId` | `BranchDetailComponent` | `authGuard` |

Data model (`branch.models.ts`) — the only fields any styling may bind to:
`Branch { branchId, name, address, contactDetails, cameras[], device }`,
`Camera { cameraId, name, rtspUrl, enabled }`,
`DeviceSummary { deviceId?, activationStatus: 'Unactivated'|'Activated', lastKnownAddress? }`.

---

## 1. Login component

- **Stitch source:** Sign-in (screen 1).
- **Maps to:** `auth/login.ts` + `auth/login.html` + `auth/login.css`.
- **Apply:** split layout — left charcoal brand panel (logo + product name + feature bullets), right
  white sign-in card; Sentinel-Green primary "Sign in" button with in-progress label; input focus
  glow; generic error styling on the existing `errorMessage` alert.
- **Bind only to:** `credentialIdentifier` (relabel field visually as desired but it is **not** an
  email field), `password`, `loading`, `errorMessage`.
- **Do NOT add:** "Remember this device", "Forgot password?", "Single Sign-On (SSO)", social login, or
  email-format semantics — none exist in FS-01. Preserve `type="password"`, `autocomplete`,
  `novalidate`, and the single generic failure message (never reveal which field was wrong).

## 2. Authenticated application shell

- **Stitch source:** Operations Overview (screen 2) — **chrome only**, not its widgets.
- **Maps to:** `shared/dashboard.ts` (currently a thin shell), and/or a new shell wrapper if one is
  introduced later around the `authGuard`-protected routes.
- **Apply:** the sidebar + top-header frame around protected content; content area max 1440px, 32px
  margins, 24px rhythm.
- **Constraint:** the dashboard's KPI tiles, charts, site status, recent-alerts, and system-health
  panels have **no backing data** — do not render them. Keep the shell's real actions: **Sign out**
  (existing `logout()` — preserves session revocation + local-token clear on any outcome) and
  navigation to **Branches**.

## 3. Sidebar

- **Stitch source:** left nav on screens 2–12.
- **Maps to:** shell navigation (within `DashboardComponent` today; a dedicated `shared` nav component
  is an acceptable refactor **if** it changes no routing behaviour).
- **Apply:** 260px fixed charcoal `#17211C` sidebar; brand mark at top; active item = Sentinel-Green
  text + 2px green left border; "Collapse" affordance optional.
- **Live nav items — ONLY the routes that exist:** Dashboard (`/dashboard`), Branches (`/branches`).
- **Do NOT add** Live monitoring, Alerts, Incidents, Cameras, Edge devices, Analytics, System health,
  Users and access, or Settings as navigation — those features do not exist (`SCREEN-INVENTORY.md`).
  Do not add dead links or "coming soon" stubs.

## 4. Top header

- **Stitch source:** top bar on screens 2–6.
- **Maps to:** shell header region.
- **Apply:** sticky ~64–72px header; page title / breadcrumb on the left; Sign-out on the right.
- **Do NOT add:** global search, site selector, date-range picker, notifications bell, help, or an
  avatar/profile menu — none are backed by a feature. A breadcrumb ("Branches / North London Branch")
  is fine as it reflects the real route hierarchy.

## 5. Branch list

- **Stitch source:** (no dedicated Stitch list screen — closest is the Camera/Alerts registry table
  pattern; use the generic table/list style from `DESIGN-SYSTEM.md` §12.)
- **Maps to:** `branches/branch-list.ts`.
- **Apply:** styled list/table rows — branch name (link), address, `DeviceStatusBadge`, right-aligned
  Edit (pencil) + Delete (trash) actions; header with "Create branch" primary button; styled
  loading / empty / error / deleted states.
- **Preserve:** the four explicit states (loading, failed, empty, loaded), the delete-confirm flow,
  and per-row accessible action labels.

## 6. Branch detail

- **Stitch source:** Branch Details (screen 3).
- **Maps to:** `branches/branch-detail.ts`.
- **Apply:** card layout — a "Branch Information" card (name, address, contact details), a "Device"
  card (status badge + `deviceId` **only when `activationStatus === 'Activated'`**), an
  Activation-Key **action** area (Regenerate), and a "Connected Cameras" list card.
- **Render only real fields.** Explicitly **omit** the Stitch mockup's Site Manager, Contact Phone,
  Timezone, Primary Gateway ID, Last Heartbeat, System Latency chart, and per-camera IP / Resolution /
  LIVE-OFFLINE. Cameras show name, RTSP (opaque, already sanitised), and Enabled/Disabled only.
- **Never** render the masked/persisted key card from the mockup — see §7.

## 7. Create Branch

- **Stitch source:** Branch Configuration Form (screen 4).
- **Maps to:** `branches/branch-create.ts` (+ `camera-config-form.ts`, `activation-key-display.ts`).
- **Apply:** "Branch Identity" card (Branch name, Address textarea, Contact details), "Camera
  Configuration" section, sticky footer with Cancel + primary "Create branch"/"Save Changes", inline
  validation styling, and the generic submit-error banner.
- **Keep single `contactDetails`** — do **not** split into Name/Email/Phone as the mockup shows.
- **Preserve** the two-phase flow: on success the form is replaced by the **one-time** Activation Key
  disclosure; the Admin leaves explicitly via "Continue to branch". Key lives in one in-memory signal
  only.

## 8. Edit Branch

- **Stitch source:** Branch Configuration Form (screen 4), reused.
- **Maps to:** `branches/branch-edit.ts`.
- **Apply:** same styled form as create.
- **Preserve:** load-then-populate; each existing camera keeps its hidden `cameraId`; add/remove
  reconciliation (≥1 camera remains); **no key disclosure** on edit; success navigates back to detail.
  Edit must never mint/regenerate a key, change device identity/activation, or show any secret.

## 9. Camera FormArray

- **Stitch source:** the repeated camera rows in Branch Configuration Form (screen 4).
- **Maps to:** `branches/camera-config-form.ts`, driven by the parent create/edit `FormArray`.
- **Apply:** each camera as a styled card/fieldset — "Camera name" + "RTSP URL" fields, per-row
  "Remove camera" (hidden on the last remaining row), and an "Add camera" button below the list.
- **Preserve:** the component owns no state; renders only name + RTSP URL; never reads/sees the hidden
  `cameraId`; never re-parses or echoes the RTSP URL (may embed credentials). Validators unchanged.

## 10. Status badges

- **Stitch source:** LIVE/OFFLINE/Active chips across screens.
- **Maps to:** `branches/device-status-badge.ts`.
- **Apply:** pill styling — Activated = green (`#EAF6EF`/`#146B3A`); Unactivated = neutral/amber;
  Unknown = grey. Keep the accessible context + description spans.
- **Preserve:** input is `activationStatus` only (never `deviceId`); an out-of-contract value renders
  as **Unknown**, never Activated. Do not add a per-camera LIVE/OFFLINE badge — no such data exists.

## 11. Activation Key display

- **Stitch source:** the Activation Key card on Branch Details (screen 3) — **visual reference for the
  card treatment only**, not its persistent/masked behaviour.
- **Maps to:** `branches/activation-key-display.ts` (used by create + regenerate flows).
- **Apply:** a prominent monospace key panel, a "Copy key" button, the "shown once" warning, and a
  "Continue"/"Done" button — styled per the mockup's key card.
- **Preserve — critical:** the key is disclosed **exactly once** from the create/regenerate response,
  held only in an in-memory signal, never stored (no storage/cookie/URL/router state/log), never in an
  href, and never re-fetched. Copy only on explicit press. **Do not** render a masked, persistent
  "regenerate" card seeded from a read (the Stitch mockup implies one — it is a security conflict, do
  not reproduce it). The Regenerate **action** lives on branch detail and reveals the new key through
  this same component.

## 12. Edit and Delete icons

- **Stitch source:** the "Edit Branch" / "Delete" controls (screen 3) and row actions.
- **Maps to:** `branch-list.ts` (per-row) and `branch-detail.ts` (page actions).
- **Apply:** 2px-stroke pencil / trash glyphs (20px list, 18px detail), adequate tap targets,
  Sentinel styling; Delete styled toward the critical/destructive treatment.
- **Preserve:** Edit is a `RouterLink`; Delete is a `<button>` that only opens the confirmation
  (never the row's name-link; never issues the request on click). Each keeps its branch-named
  `aria-label` + `title`; the SVG stays `aria-hidden`.

## 13. Delete confirmation

- **Stitch source:** (no dedicated Stitch dialog — use `DESIGN-SYSTEM.md` §13 modal pattern.)
- **Maps to:** `branches/branch-delete-confirm.ts`.
- **Apply:** centred modal card on a 40%-opacity charcoal backdrop, title naming the branch, the
  effects list, Cancel (ghost) + Delete (destructive, in-flight label), right-aligned.
- **Preserve:** `role="dialog"`, `aria-modal`, `aria-labelledby`/`describedby`, focus-to-Cancel on
  open; owns no HTTP; parent performs the delete on `confirmed`, closes on `cancelled`; destructive
  button disabled while in flight. Keep the "physical Agent is not remotely wiped" note.

## 14. Loading and error states

- **Stitch source:** "Action Required" / "Sync Error" banners (screens 3–4), in-progress buttons.
- **Maps to:** the existing status blocks in every branch/auth component.
- **Apply:** styled inline banners (icon + generic message), optional skeleton rows for lists, and
  disabled in-progress buttons ("Signing in…", "Creating…", "Saving…", "Deleting…").
- **Preserve:** messages stay **generic** — no backend error text, status codes, or echoed field
  values (an RTSP URL or key must never leak into an error). 401 is handled globally by the
  session-expiry interceptor (redirect to `/login`); 404 → safe not-found; anything else → generic.

---

## 15. Dashboard summary (real data)

- **Stitch source:** Operations Overview (screen 2) — stat-tile row + "Recent Critical Alerts" table +
  the general card/panel treatment.
- **Maps to:** new `dashboard/dashboard-summary.ts` (+html/css), replacing the old thin
  `shared/dashboard.ts` shell entirely; `dashboard/dashboard.service.ts` calls
  `GET /api/v1/dashboard/summary`.
- **Apply:** a KPI-tile row (today's accepted Alerts / configured maximum, remaining capacity,
  suppressed-today total, gun/knife breakdown), a quota **progress bar** (new `.progress` primitive,
  §19) showing accepted-vs-maximum, the current Branch's local date + next-reset time, a small (5–10
  row) real recent-Alerts list linking into `/alerts`, and Device/Camera count tiles.
- **Do NOT add:** the multi-site selector, the alert-trend bar chart, Open Incidents / Avg Response
  tiles, Site Status list, System Health meters — none has backing data (`SCREEN-INVENTORY.md` row 2).
- **Preserve:** bounded polling (10–15s) with `takeUntilDestroyed()`, manual refresh control, a
  last-successful-refresh timestamp, and an explicit "quota not yet available today" state (distinct
  from a confirmed `0`) when no `BranchDailyAlertQuota` row exists yet for the current local day.

## 16. Alert list

- **Stitch source:** Alerts Management (screen 8) — filter bar + table layout pattern only.
- **Maps to:** new `alerts/alert-list.ts` (+html/css), `alerts/alert.service.ts` calling
  `GET /api/v1/alerts`.
- **Apply:** the filter-bar visual pattern (date range, class, Branch, Camera, Status,
  snapshot-availability — using the existing `.field`/`select`/input styling, no new form-control
  primitives needed), the table pattern (new `.table` primitive, §19) with columns: detected time,
  class (`weapon-class-badge`), confidence, Branch, Camera, Device, Status, snapshot indicator, open
  action; pagination controls at the foot.
- **Do NOT add:** severity, assignee/"Assign To", bulk-select + Confirm/False-Positive/bulk actions,
  Export, global search, min-confidence slider (`SCREEN-INVENTORY.md` row 8 — none exist).
- **Preserve:** server-side pagination (never load the full Alert table into the browser), filter state
  round-tripped through the URL's query parameters, default = current Branch-local day + newest-first,
  `track` by `alertId`, a clear-filters action, and the existing loading/empty/error state discipline
  (`empty-state`/`banner--error` primitives, unchanged).

## 17. Alert detail / snapshot placeholder

- **Stitch source:** Alert Review (screen 9) — right-side metadata-panel layout pattern only.
- **Maps to:** new `alerts/alert-detail.ts` (+html/css), route-param-driven, calling
  `GET /api/v1/alerts/{id}`.
- **Apply:** a metadata card (detected/received timestamps, class badge, confidence, Branch, Camera,
  Device, Status badge, delivery latency), styled per the mockup's right-side panel card treatment.
  Where the mockup places a live-recording player, render a **snapshot placeholder card**: a neutral
  icon + the exact text "Snapshot evidence is not available for this Alert" whenever
  `snapshotState !== 'available'` (every Alert in this increment) — never a broken `<img>`, never a
  fabricated/stock CCTV image. Structure this as its own child component so a future real-evidence
  increment only swaps this one piece.
- **Do NOT add:** the live-recording player, HUD bounding-box overlay, Event Timeline scrubber, Audit
  Trail & Operator Activity log, Confirm Threat / False Positive / Escalate to Site Team buttons,
  operator-comment box, Print/Share (`SCREEN-INVENTORY.md` row 9 — none exist; no approved
  status-transition feature exists).
- **Preserve:** loading/notFound/failed/loaded signal states mirroring `branch-detail.ts` exactly; a
  404 from the Backend renders a safe "Alert not found" state, never a raw error/status code.

## 18. Sidebar navigation additions

- **Stitch source:** left nav on screens 2/8/9 ("Overview", "Alerts").
- **Maps to:** `shell.ts`'s existing navigation list (currently Dashboard + Branches only, per §3
  above).
- **Apply:** add **Alerts** as a live nav item alongside the existing Dashboard/Branches, same
  260px-sidebar / active-item-green-left-border treatment already implemented.
- **Do NOT add:** Live monitoring, Incidents, Cameras, Edge devices, Analytics, System health, Users
  and access, Settings — still unbacked by any feature (`SCREEN-INVENTORY.md`).

## 19. New design-token primitives (first consumers: §16/§15)

- **`.table`/`.table__head`/`.table__row`** — added to `styles.css`; consumes only existing color/
  spacing/typography tokens (`--color-border`, `--color-surface-subtle`, `--text-sm`, `--space-*`); no
  new colors introduced.
- **`.progress`/`.progress__bar`** — quota progress bar; fill color uses `--color-primary` under the
  configured maximum, switches to `--color-danger`-family tokens only once the maximum is reached
  ("Daily quota reached" state) — never conveys the exhausted state by color alone, always paired with
  the "X / Y Alerts used" text.
- **`.badge--gun`/`.badge--knife`/`.badge--unknown`/`.badge--suppressed`** — modifiers on the existing
  `.badge` primitive; each badge always renders an icon **and** text label, never color-only, per the
  task's accessibility requirement.

---

## Global rules (restated — binding on all of the above)

1. **Angular, not React.** Stitch output is a visual spec; reproduce it with Angular templates + CSS.
   Never paste generated React/Tailwind code.
2. **Do not modify Backend APIs**, DTOs, or contracts.
3. **Do not modify the Agent project** (`agent/…`) or any backend source.
4. **No fake functionality** and **no navigation to non-existent features.**
5. **Preserve** authentication + session-expiry handling; branch create/edit/delete behaviour; camera
   add/edit/remove behaviour; Activation-Key one-time-disclosure behaviour.
6. **Never expose** Activation Keys (beyond the single disclosure), device shared secrets, JWTs,
   passwords, or internal IDs (`DeviceRecordId`, `keyId`, hashes).
7. **Do not install dependencies** and **do not modify Angular source files** as part of this design
   documentation task — this map is the plan; implementation is a separate, approved step.
