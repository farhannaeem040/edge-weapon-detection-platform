# `branches`

Branch management feature (IP-01 §3; FS-02 Increment A).

## Contents

- `branch.service.ts` — `BranchService`, the client for the Branch/Device endpoints (T-26/T-27/T-28).
  It calls the approved endpoints and unwraps the standard `{ success, message, data }` envelope, and
  does nothing else: no redaction, no derivation of activation state, no caching. The Backend's
  response *is* the contract, so reshaping it here would put a second, divergent copy of FS-02's
  disclosure rules in the browser. A 404 from the read and regenerate calls becomes `null` (a
  documented outcome, not a fault); every other error propagates.
- `branch.models.ts` — the request/response types mirroring the Backend contract.
- `branch.validators.ts` — the RTSP URL and required-field validators used by the create form.
- `branch-list.ts` — `BranchListComponent`: every branch with its cameras and device status, plus the
  empty state (T-26).
- `branch-detail.ts` — `BranchDetailComponent`: one branch, its cameras, its device status, and the
  regenerate action with its confirmation step (T-26/T-28).
- `branch-create.ts` — `BranchCreateComponent`: the creation form (T-27).
- `camera-config-form.ts` — `CameraConfigFormComponent`: one camera's fields, driven by a reactive
  `FormArray` so an Admin can add or remove cameras. At least one camera is required (FS-02 §12).
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
| `/branches/:branchId` | `BranchDetailComponent` | `authGuard` |

`/branches/new` is declared before `/branches/:branchId`: the router takes the first match, and the
parameterised route would otherwise capture `new` as a branch id.

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
