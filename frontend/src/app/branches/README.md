# `branches`

Branch management feature (IP-01 §3; FS-02 Increment A). Branch/camera **editing and deletion** are
added by IP-03 (FS-03).

## Contents

- `branch.service.ts` — `BranchService`, the client for the Branch/Device endpoints
  (T-26/T-27/T-28, plus `update` (T-45) and `delete` (T-46)). It calls the approved endpoints and
  unwraps the standard `{ success, message, data }` envelope, and does nothing else: no redaction, no
  derivation of activation state, no caching. The Backend's response *is* the contract, so reshaping
  it here would put a second, divergent copy of FS-02/FS-03's disclosure rules in the browser. A 404
  from the read, update, delete, and regenerate calls becomes `null` (a documented outcome, not a
  fault); every other error propagates.
- `branch.models.ts` — the request/response types mirroring the Backend contract, including
  `UpdateBranchRequest`/`UpdateCameraRequest` (T-45), whose optional per-camera `cameraId` is the
  whole identity contract of an edit: present → update in place; absent → add; an omitted existing
  camera → remove (FS-03 §1.3, §5.2).
- `branch.validators.ts` — the RTSP URL and required-field validators, shared by the create and edit
  forms.
- `branch-list.ts` — `BranchListComponent`: every branch with its cameras and device status, the
  empty state (T-26), and the per-row **Edit** (pencil) and **Delete** (trash) actions (T-45/T-46).
- `branch-detail.ts` — `BranchDetailComponent`: one branch, its cameras, its device status, the
  regenerate action with its confirmation step (T-26/T-28), and the **Edit**/**Delete** actions for
  the branch (T-45/T-46).
- `branch-create.ts` — `BranchCreateComponent`: the creation form (T-27).
- `branch-edit.ts` — `BranchEditComponent`: the edit form (T-45). Loads the branch, populates its
  fields and cameras (keeping each existing camera's id in a hidden control), reconciles cameras
  (edit/add/remove, at least one remaining), and saves via `PUT`. Unlike creation there is **no key
  disclosure**: an edit never mints, regenerates, or reveals an Activation Key and never touches
  device identity or activation state, so a successful save simply returns to the branch detail view.
- `branch-delete-confirm.ts` — `BranchDeleteConfirmComponent`: the accessible, labelled modal dialog
  that confirms a hard delete (T-46). It owns no HTTP — the list/detail parent performs the delete on
  `confirmed` and closes on `cancelled` — and spells out, in the branch's name, that the delete is
  irreversible and removes the branch's cameras, Device registration, and Activation Keys, while a
  physical Agent is **not** remotely wiped (FS-03 §6.3, §7.4).
- `camera-config-form.ts` — `CameraConfigFormComponent`: one camera's fields, driven by a reactive
  `FormArray` so an Admin can add or remove cameras. Reused unchanged by the edit form — it renders
  only name and RTSP URL and never sees the hidden `cameraId`. At least one camera is required
  (FS-02 §12, FS-03 §5.2).
- `activation-key-display.ts` — `ActivationKeyDisplayComponent`: the single disclosure of a complete
  plaintext Activation Key (T-27, reused unchanged by T-28's regeneration flow — one disclosure
  treatment, one set of storage rules, one copy path).
- `device-status-badge.ts` — `DeviceStatusBadgeComponent`: Unactivated/Activated (T-29).
- `branch.routes.ts` — the branch route paths, defined once so the login landing, the dashboard's
  navigation, and the list's per-branch links cannot drift apart from the routes themselves.

## Routes

| Route | Component | Guard |
|-------|-----------|-------|
| `/branches` | `BranchListComponent` | `authGuard` |
| `/branches/new` | `BranchCreateComponent` | `authGuard` |
| `/branches/:branchId/edit` | `BranchEditComponent` | `authGuard` |
| `/branches/:branchId` | `BranchDetailComponent` | `authGuard` |

`/branches/new` is declared before `/branches/:branchId`: the router takes the first match, and the
parameterised route would otherwise capture `new` as a branch id. `/branches/:branchId/edit` cannot
collide with the two-segment detail route (it has three segments) but is kept beside the other write
routes for readability.

## Edit / delete actions (FS-03)

- **Edit** and **Delete** appear as icon controls beside every branch in the list, and as actions on
  the branch detail page. Each is a real, keyboard-focusable control (Edit is a link, Delete is a
  `<button>` — never the branch-name link) with an `aria-label` and matching `title` naming the
  branch; the SVG glyph itself is `aria-hidden`. Adequate click/tap targets are applied.
- **Deletion is hard and irreversible in this prototype.** Confirming removes the branch's cameras,
  its Device, its Activation Keys, and the branch itself. No remote Agent is contacted (FS-03 §7.4).
  A future milestone with Alerts/Events/Reports and audit-retention requirements may need soft
  deletion or deletion restrictions; those entities do not exist yet.
- **Editing preserves device identity and activation state.** It never mints/regenerates an
  Activation Key, never changes the public Device ID, and the edit form neither displays nor requests
  any key, device id, or secret.

## Security constraints

- **The Activation Key is disclosed exactly once and held only in memory.** The Backend returns it
  only from the create and regenerate responses and can never re-derive it — it stores only the
  `keyId` and a salted hash of the `secret` (FS-02 §1.4, §11). The key is therefore never written to
  `sessionStorage`, `localStorage`, IndexedDB, a cookie, the URL, a query parameter, or router state;
  never logged; and never placed in a link's href. It lives only in the parent's in-memory signal,
  which dies with the component. Nothing re-fetches it — no endpoint would answer, by design. The
  recovery for missing it is regeneration, not a second look.
- **Copying is the Admin's decision.** The key reaches the system clipboard only on an explicit
  press, never automatically on render.
- **Regeneration is addressed by branch id**, not a Device ID — see `regenerateActivationKey`. An
  unactivated Device has no `DeviceId`, and the internal `DeviceRecordId` is never exposed to any
  client (FS-02 §1.3), so the branch id is the only always-present, client-visible handle to a
  branch's single Device.
- **No `deviceId` is rendered for an unactivated Device** — it does not exist until first activation
  (FS-02 AC-7).
- **Nothing here logs a request or a response.** A create request body carries an RTSP URL that may
  embed credentials, and the create/regenerate response carries the plaintext key (FS-02 §11,
  ARCH-001 §15.6).
- The `authGuard` on these routes is a UX control, **not** a security boundary (FS-01 §10). The
  Backend independently validates the JWT and the non-revoked `AdminSession` on every underlying call.
