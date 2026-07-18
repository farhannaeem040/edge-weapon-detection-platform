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
