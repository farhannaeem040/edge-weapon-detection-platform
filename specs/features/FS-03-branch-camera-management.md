# Feature Specification: Branch & Camera Management (Edit / Delete)

| Field | Value |
|-------|-------|
| Feature ID | FS-03 |
| Title | Branch & Camera Management (Edit / Delete) |
| Status | Approved |
| Related SRS Requirements | FR-BRN-001 (branch/camera data captured at creation is the same data edited here), FR-BRN-007, BR-002, CON-007, NFR-SEC-001, NFR-MNT-001, NFR-USB-001 |
| Related Architecture Sections | ARCH-001 §10.1 (BranchController/DeviceController, BranchService/DeviceService), §10.5 (Angular Branch Management module), §13.1 (Branch, Camera, Device entities), §13.3 (Credential/Secret Storage), §14.1 (Backend API), §15.1 (Trust Boundaries), §16.4 (Device identity retention) |
| Related ADRs | ADR-009 (uniform envelope), ADR-013 (session revocation — reused for auth), ADR-015 (Device identity retained; secret rotates only on activation, never on edit) |
| Owner | Farhan Naeem |
| Dependencies | FS-01 (Admin authentication), FS-02 (Branch/Camera/Device/Activation Key data model and identity split — established and delivered by IP-01) |
| Approval basis | Explicit user approval of this feature scope (this document records that approval). No SRS or ARCH-001 requirement is added or changed. |

---

## 1. Purpose and Scope

This feature specifies the behavior required for a System Administrator to **edit** an existing branch and its cameras, and to **delete** a branch, through the Dashboard. It extends the branch/camera/device model FS-02 established and IP-01 delivered; it introduces no new entity, no new device identity concept, and no new security mechanism.

FS-02 delivered branch/camera **creation**, activation-key generation/regeneration, and read views. It deliberately left ongoing lifecycle management (editing an existing branch, removing a branch) unspecified. FS-03 fills exactly that gap and nothing more.

### 1.1 What This Feature Adds

| Capability | SRS/ARCH Basis |
|------------|----------------|
| Edit a branch's name, address, and contact details | FR-BRN-001 (same fields captured at creation) |
| Edit an existing camera's name and RTSP URL | FR-BRN-001 |
| Add cameras to an existing branch | FR-BRN-001, ARCH-001 §13.1 ("one or more Camera records") |
| Remove cameras from an existing branch, preserving at least one | FR-BRN-001, ARCH-001 §13.1, FS-02 §12 (a branch always has ≥1 camera) |
| Delete a branch and its dependent data through an explicit confirmation flow | Prototype lifecycle management; no SRS requirement forbids it, and CON-007's single-device-per-branch model makes the dependent set finite and known |
| Show Edit and Delete controls beside every branch in the list, and on branch detail | ARCH-001 §10.5 (Branch Management module), NFR-USB-001, NFR-MNT-001 |

### 1.2 What This Feature Does Not Touch

FS-03 is strictly a management layer over the existing model. It does **not**:

- change the Device identity model (FS-02 §1.3) in any way;
- alter activation, reactivation, or activation-key generation/regeneration (FS-02 §5);
- introduce any Agent-facing behavior, Agent code, or IP-02 (Jetson Agent) work;
- introduce alerts, events, reports, health records, or any entity not already in the delivered model;
- add soft deletion, audit trails, or retention policy (see §8 — these are recorded as future needs, not implemented).

### 1.3 Relationship to the Existing Public Contract

An important fact established by inspecting the delivered code (IP-01), not assumed here:

- **`CameraId` is already a public identifier.** The delivered `CameraResponseDto` returns `cameraId` (the external `Camera.CameraId` GUID) on every read, and the Angular `Camera` model already carries it. FS-03 therefore **reuses** this existing public identifier to distinguish cameras across an edit; it introduces no new identifier.
- **`DeviceRecordId` remains internal.** It is never returned by any endpoint and FS-03 never exposes it.
- **The Activation Key's `keyId` and secret hash remain internal.** No FS-03 response carries them.

## 2. Actors

| Actor | Description |
|-------|-------------|
| Admin User | The single authenticated account (BR-001, FS-01) that edits and deletes branches through the Dashboard. |
| ASP.NET Core Backend | Validates the edit/delete request, applies it transactionally, and returns a safe representation. |

No Agent actor participates in this feature. Editing or deleting a branch on the Backend does not contact, wipe, or modify any physical Jetson device (§7.4).

## 3. Preconditions

- The Admin is authenticated with a valid, non-revoked session (FS-01, ADR-013).
- The branch being edited or deleted already exists (created via FS-02).
- Every edit/delete operation is reachable only through the Admin-authenticated Dashboard API; there is no anonymous or Agent-facing path to either (FR-BRN-006, NFR-SEC-001).

## 4. Functional Behavior

| # | Behavior | Requirement Basis |
|---|----------|-------------------|
| 1 | The Admin can edit a branch's name. | FR-BRN-001 |
| 2 | The Admin can edit a branch's address. | FR-BRN-001 |
| 3 | The Admin can edit a branch's contact details. | FR-BRN-001 |
| 4 | The Admin can edit an existing camera's name. | FR-BRN-001 |
| 5 | The Admin can edit an existing camera's RTSP URL. | FR-BRN-001 |
| 6 | The Admin can add one or more cameras to an existing branch; each added camera receives a new camera identity. | FR-BRN-001, ARCH-001 §13.1 |
| 7 | The Admin can remove cameras from a branch; removing a camera deletes only that camera. | FR-BRN-001 |
| 8 | At least one camera must remain on a branch after an edit; an edit that would leave zero cameras is rejected. | ARCH-001 §13.1, FS-02 §12 |
| 9 | The Admin can delete a branch through an explicit confirmation flow that warns the action cannot be undone. | Prototype lifecycle management |
| 10 | Edit and Delete controls appear beside every branch in the branch list. | ARCH-001 §10.5, NFR-USB-001 |
| 11 | Edit and Delete actions appear on the branch detail page. | ARCH-001 §10.5, NFR-USB-001 |

## 5. Detailed Workflows

### 5.1 Edit a Branch

1. The Admin opens a branch's edit view from the branch list or the branch detail page.
2. The Dashboard loads the current branch (name, address, contact details) and all its cameras (each carrying its public `cameraId`, name, and displayed RTSP URL) via the existing read endpoint.
3. The Admin changes any of: branch name, address, contact details; any existing camera's name or RTSP URL; adds new cameras; removes existing cameras (while at least one remains).
4. The Dashboard submits `PUT /api/v1/branches/{branchId}` with the full desired end-state: the branch scalar fields and the complete intended camera collection. Each camera in the collection either carries an existing `cameraId` (update in place) or carries none (add as new).
5. The Backend validates the request (§6), then in a single transaction updates the branch fields, updates existing cameras in place (preserving their `cameraId`), adds new cameras (each with a fresh `cameraId`), and removes any existing camera whose `cameraId` was not present in the request.
6. The Device record, its `DeviceRecordId`, its public `DeviceId`, its `ActivationStatus`, its `ProtectedSharedSecret`, and all Activation Key records and their statuses are left **entirely unchanged** (§5.3).
7. The Backend returns the updated branch in the same safe read shape as `GET /api/v1/branches/{id}` (no plaintext key, no secret, no internal identifiers).
8. The Dashboard navigates back to the branch detail view.

### 5.2 Camera Reconciliation Semantics

The camera collection in an update request is the **desired end-state**, reconciled against the stored cameras by `cameraId`:

| Request camera | Stored match | Action |
|----------------|--------------|--------|
| Carries a `cameraId` that exists on this branch | Yes | Update that camera's name and RTSP URL in place; `cameraId` preserved. |
| Carries no `cameraId` | — | Add a new camera with a freshly generated `cameraId`. |
| A stored camera whose `cameraId` is absent from the request | — | Remove that camera (delete only it). |
| Carries a `cameraId` that does not exist, or belongs to a different branch | No / foreign | Reject the entire request (§6, §5.4 conflict handling). No partial change. |
| Two request cameras carry the same `cameraId` | — | Reject the entire request (duplicate). No partial change. |

### 5.3 Explicit Preservation Rules (Edit)

Editing a branch **must preserve, unchanged**:

- the existing public Branch ID (`BranchId`);
- the existing Device record and its internal `DeviceRecordId`;
- the existing public Device ID (`DeviceId`), including its null state for an unactivated device;
- the Device's activation status (`ActivationStatus`);
- the Device's protected shared secret (`ProtectedSharedSecret`);
- all Activation Key records for the device, and the current key's status;
- the `cameraId` of every camera that is edited (as opposed to added or removed).

Editing **must never**:

- regenerate or invalidate an Activation Key;
- deactivate a Device or change its activation status;
- rotate, clear, or alter the Device shared secret;
- change the public Device ID;
- expose any internal identifier (`DeviceRecordId`, activation-key `keyId`, secret hash) or any secret.

This is a direct consequence of ADR-015: the Device identity is retained across its whole lifecycle, and the shared secret rotates **only** on activation/reactivation — an edit is neither.

### 5.4 Edit Error Handling

| Case | Outcome |
|------|---------|
| Unknown branch | 404, safe not-found envelope; nothing changed. |
| Missing/blank required branch field, or field over its length limit | 400, generic validation envelope; nothing changed. |
| Zero cameras in the request | 400, generic validation envelope; nothing changed. |
| A camera with a blank name, blank RTSP URL, or a non-`rtsp://` URL | 400, generic validation envelope; nothing changed. |
| A request `cameraId` that belongs to another branch, or matches no camera on this branch | Rejected without applying any change. Modelled as a validation failure (400) — the project's existing convention treats a DTO-valid-but-business-invalid request as a 400 (see `BranchController.Create`). No internal identifier is echoed in the response. |
| Two request cameras sharing one `cameraId` | Rejected as above (400); nothing changed. |
| No valid Admin session | 401, before any business logic (ADR-013). |

A failed update leaves all Branch, Camera, Device, and Activation Key data exactly as it was — guaranteed by applying the whole update in one transaction (§11).

### 5.5 Delete a Branch

1. The Admin selects Delete beside a branch (list) or on the branch detail page.
2. The Dashboard opens an explicit confirmation panel/dialog stating: which branch is being deleted; that the operation cannot be undone; that the branch's cameras, device registration, and activation keys will be deleted; and that a physical Agent is **not** remotely wiped (§7.4).
3. The Admin either cancels (no request is sent, nothing changes) or confirms.
4. On confirmation, the Dashboard calls `DELETE /api/v1/branches/{branchId}`.
5. The Backend, in a single transaction, deletes the branch's Activation Key records, then its Device record, then its Cameras, then the Branch itself, and commits atomically.
6. The Backend returns a safe success envelope containing no deleted entity data and no credentials.
7. The Dashboard returns to the branch list; the deleted branch no longer appears.

### 5.6 Delete Semantics (Prototype)

For the current prototype, branch deletion is a **hard delete** performed transactionally. It removes the branch's currently modelled dependent data — Cameras, Device, Activation Keys, and the Branch — permanently.

- Deletion works for both **activated** and **unactivated** branches; no approved requirement prohibits deleting an activated branch.
- Deleting an activated branch **invalidates Backend recognition of that Device**: once the Device and its shared secret are gone, no future Agent request bearing that `DeviceId`/secret can be authenticated by the Backend, because there is no record to authenticate against. (The operational endpoints that would perform such authentication are future features; FS-03 only guarantees the record is gone.)
- Deletion **does not** contact, wipe, or remotely modify any physical Jetson device (§7.4). A previously activated Agent may still hold its local credentials, but the Backend will no longer recognise them.
- No deleted Device ID, shared secret, activation key, or internal identifier is returned or logged during deletion.

### 5.7 Delete Error Handling

| Case | Outcome |
|------|---------|
| Unknown branch | 404, safe not-found envelope. |
| No valid Admin session | 401, before any business logic. |
| Any failure partway through the deletion | Entire operation rolls back; the branch and all dependents remain exactly as they were (§11). |

## 6. Validation Rules

- A branch-update request must include a non-blank name, address, and contact details, each within the existing Domain length limits (`Branch.NameMaxLength`, `AddressMaxLength`, `ContactDetailsMaxLength`).
- A branch-update request must include at least one camera.
- Each camera must have a non-blank name (≤ `Camera.NameMaxLength`) and a non-blank, absolute `rtsp://` RTSP URL (≤ `Camera.RtspUrlMaxLength`) — the same rules the create path already enforces (`BranchService.EnsureValidRtspUrl`).
- Every existing `cameraId` in the request must belong to the branch being edited; an unknown or foreign `cameraId` rejects the whole request.
- No two request cameras may carry the same `cameraId`.
- No request value is ever echoed back in an error message (an RTSP URL may embed credentials — ARCH-001 §15.6, FS-02 §11).

## 7. Backend Responsibilities

### 7.1 Update

- Expose `PUT /api/v1/branches/{branchId}`, protected by the standard Admin JWT + active-session policy (ADR-013).
- Validate the request per §6 before any persistence.
- Apply the branch-scalar update, the per-camera reconciliation (§5.2), in one transaction (§11).
- Preserve the Device and all Activation Key records unchanged (§5.3).
- Return the updated branch in the existing safe read shape (`BranchResponseDto.ForRead` equivalent) — no plaintext key, no secret, no `DeviceRecordId`.

### 7.2 Delete

- Expose `DELETE /api/v1/branches/{branchId}`, protected by the same policy.
- Delete the branch's Activation Keys, Device, Cameras, and the Branch in one transaction, in an explicit dependency-safe order (§11), and commit atomically.
- Return a safe success envelope carrying no deleted entity data.

### 7.3 Security

- Both endpoints require a valid Admin session; neither is anonymous or Agent-facing (NFR-SEC-001).
- No response from either endpoint contains a shared secret, activation key, key hash, `DeviceRecordId`, or any EF entity — only the safe read DTO (update) or a bare success envelope (delete).
- No secret or request value is written to any log (ARCH-001 §15.6).

### 7.4 No Remote Agent Cleanup

Neither endpoint attempts to contact, notify, wipe, or reconfigure a physical Jetson Agent. This is out of scope and is **not** invented here. The Backend only removes its own records; a deleted Device is simply no longer recognised by the Backend.

## 8. Future Retention / Soft-Delete Consideration (Not Implemented)

The current model contains only Branch, Camera, Device, and Activation Key entities. A hard delete is therefore complete and safe: there is no Alert, Event, Report, or audit record that a deletion could orphan or that a retention policy must protect.

When later features introduce Alerts, Events, Reports, and audit/retention requirements, deleting a branch that owns historical alerts may need to become a **soft delete** or a **restricted delete** (e.g., forbidding deletion of a branch with retained alerts, or preserving alert history under a tombstoned branch). Those entities do not exist in the current milestone, so FS-03 implements a hard delete and records this as a known future need — it does **not** claim soft deletion, audit retention, or any such behavior now.

## 9. Angular Dashboard Responsibilities

- Provide a protected branch-edit route (`/branches/:branchId/edit`) that loads the branch, populates name/address/contact-details and every camera (preserving each existing camera's `cameraId`), and lets the Admin edit fields, add cameras, and remove cameras while at least one remains.
- Reuse the existing camera form logic and validators where practical rather than duplicating them.
- Submit the exact `PUT /api/v1/branches/{branchId}` contract, navigate back to branch detail on success, show a generic safe error on failure, and preserve non-sensitive entered values after a failure.
- Never display or request an Activation Key, key status, shared secret, protected secret, `DeviceRecordId`, password, or JWT in the edit view.
- Show an accessible Edit control (pencil icon) beside every branch in the list, and an Edit action on branch detail, both linking to the edit route.
- Show an accessible Delete control (trash icon) beside every branch in the list, and a Delete action on branch detail, both opening an explicit confirmation flow (§5.5).
- The confirmation must name the branch, warn the action is irreversible, state that cameras/device/activation keys are deleted and that a physical Agent is not remotely wiped, offer Cancel and Delete, send no request on cancel, disable the destructive button while the request is in flight, and on success return to the branch list without ever displaying deleted credentials.
- Handle 401 via the existing session-expiry interceptor, 404 with a safe not-found result, and any other failure with a generic error — never surfacing raw SQL, exception text, or Backend error strings.
- Contain no business logic beyond form assembly, submission, confirmation, and navigation (NFR-MNT-001).

### 9.1 Accessibility

Every Edit/Delete icon control must have: a visible tooltip/title, an `aria-label` that includes the branch's name for context, keyboard operability, and an adequate click-target size. Icons are decorative reinforcements of an accessible label, never the sole carrier of meaning.

## 10. API Contracts (Feature-Specification Level)

All responses use the uniform envelope (`{success, message, data}` / `{success, message, errorCode}`) per ARCH-001 §14.3 / ADR-009.

### 10.1 `PUT /api/v1/branches/{branchId}` — Update

| Aspect | Detail |
|--------|--------|
| Auth | Valid Admin JWT + active session |
| Request | `{ name, address, contactDetails, cameras: [{ cameraId?, name, rtspUrl }, ...] }` — `cameraId` present for an existing camera to update, absent for a new camera |
| Success | 200; `data` is the updated branch in the safe read shape (same as `GET /api/v1/branches/{id}`): `{ branchId, name, address, contactDetails, cameras: [{ cameraId, name, rtspUrl, enabled }], device: { deviceId?, activationStatus, lastKnownAddress? } }`. No plaintext key, no secret, no `DeviceRecordId`. |
| Failure — validation (blank/oversized field, zero cameras, invalid RTSP, unknown/foreign/duplicate `cameraId`) | 400, generic validation envelope; nothing changed |
| Failure — no/invalid session | 401 |
| Failure — unknown branch | 404 |

### 10.2 `DELETE /api/v1/branches/{branchId}` — Delete

| Aspect | Detail |
|--------|--------|
| Auth | Valid Admin JWT + active session |
| Request | None beyond the branch id in the route |
| Success | 200, success envelope with no entity data (`data` null/absent) |
| Failure — no/invalid session | 401 |
| Failure — unknown branch | 404 |

## 11. Transactional Guarantees

- **Update** applies branch-field changes and the full camera reconciliation (updates, additions, removals) within a single SQL Server transaction. A failure at any point rolls the whole thing back, leaving Branch, Camera, Device, and Activation Key data unchanged. The Device and Activation Key rows are never written by the update path at all.
- **Delete** removes Activation Keys, Device, Cameras, and the Branch within a single SQL Server transaction, in explicit dependency order (children before parents). A failure at any point rolls the whole thing back, leaving everything as it was.
- Because these guarantees depend on real SQL Server locking, constraint, and transaction semantics, they are verified by integration tests against a real SQL Server database, consistent with IP-01 §9 (EF InMemory/SQLite are not used to claim transactional/relational behavior).

## 12. Security Rules

- Both endpoints are reachable only with a valid Admin session; there is no anonymous or Agent path (NFR-SEC-001, FR-BRN-006).
- Editing never rotates the shared secret, never regenerates an activation key, and never changes the Device identity or status (§5.3, ADR-015).
- No response or log from either endpoint contains a shared secret, activation key, key hash, `DeviceRecordId`, or EF entity.
- No request value (including an RTSP URL, which may embed credentials) is echoed into any error message or log (ARCH-001 §15.6, FS-02 §11).
- Deletion exposes no Device credentials, even when deleting an activated branch (§5.6).

## 13. Acceptance Criteria

| # | Acceptance Criterion | Traces To |
|---|----------------------|-----------|
| AC-1 | Editing a branch's name, address, and contact details persists the new scalar values and returns them in the safe read shape. | FR-BRN-001; §5.1 |
| AC-2 | Editing an existing camera's name and RTSP URL persists the changes and preserves that camera's `cameraId`. | FR-BRN-001; §5.2, §5.3 |
| AC-3 | Adding a camera during an edit creates a new camera with a new `cameraId`; the existing cameras are unaffected. | FR-BRN-001; §5.2 |
| AC-4 | Removing a camera during an edit deletes only that camera; the others (and their `cameraId`s) are unaffected. | FR-BRN-001; §5.2 |
| AC-5 | An edit that would leave a branch with zero cameras is rejected (400); the branch is unchanged. | ARCH-001 §13.1, FS-02 §12; §5.4 |
| AC-6 | A failed update (any validation or persistence failure) leaves all Branch, Camera, Device, and Activation Key data unchanged (transactional rollback). | §11 |
| AC-7 | After any successful edit, the Device's `DeviceRecordId`, public `DeviceId`, `ActivationStatus`, and `ProtectedSharedSecret` are unchanged. | FR-BRN-007, ADR-015; §5.3 |
| AC-8 | After any successful edit, the branch's Activation Key records and the current key's status are unchanged; no key is regenerated or invalidated. | ADR-015; §5.3 |
| AC-9 | A request `cameraId` that is unknown, belongs to another branch, or is duplicated within the request is rejected without applying any change. | §5.2, §5.4 |
| AC-10 | Deleting a branch removes its Cameras, Device, Activation Keys, and the Branch, for both activated and unactivated branches. | §5.5, §5.6 |
| AC-11 | Deleting a branch does not affect any other branch or its dependent data. | §11 |
| AC-12 | A deleted Device's credentials are no longer recognised by the Backend (there is no record to authenticate against). | §5.6 |
| AC-13 | Both endpoints require a valid Admin session; an unauthenticated or session-invalid request is rejected (401) before any business logic. | NFR-SEC-001, ADR-013; §7.3 |
| AC-14 | No update or delete response, and no log, contains a shared secret, activation key, key hash, `DeviceRecordId`, or EF entity. | §12 |
| AC-15 | Deleting a branch requires explicit Admin confirmation that warns the action cannot be undone before any request is sent; cancelling sends no request. | §5.5 |
| AC-16 | Edit and Delete controls appear beside every branch in the list and on branch detail, each with an accessible label containing the branch's name and keyboard operability. | ARCH-001 §10.5, NFR-USB-001; §9.1 |
| AC-17 | No edit or delete UI displays or requests an Activation Key, key status, shared secret, protected secret, `DeviceRecordId`, password, or JWT. | §9, §12 |

## 14. Test Scenarios

### Backend

- T-A1: Edit branch scalar fields → persisted; read shape returned.
- T-A2: Edit an existing camera's name and RTSP URL → `cameraId` preserved.
- T-A3: Add one camera → new `cameraId` assigned; existing cameras unchanged.
- T-A4: Add multiple cameras → all get new identities.
- T-A5: Remove one camera → only it is gone.
- T-A6: Replace several camera values in one request → all applied atomically.
- T-A7: Attempt to remove the last camera (zero cameras in request) → 400; unchanged.
- T-A8: Unknown branch → 404.
- T-A9: Foreign `cameraId` (belongs to another branch) → rejected; unchanged.
- T-A10: Duplicate `cameraId` within the request → rejected; unchanged.
- T-A11: Invalid RTSP URL → 400; unchanged.
- T-A12: Simulated persistence failure mid-update → full rollback.
- T-A13: After edit, `DeviceRecordId` unchanged.
- T-A14: After edit, public `DeviceId` unchanged.
- T-A15: After edit, activation status unchanged.
- T-A16: After edit, `ProtectedSharedSecret` unchanged.
- T-A17: After edit, Activation Key records and statuses unchanged.
- T-A18: Update without a valid session → 401.
- T-D1: Delete an unactivated branch → Cameras, Device, Activation Keys, Branch all removed.
- T-D2: Delete an activated branch → all removed, including the activated Device.
- T-D3: Delete an unknown branch → 404.
- T-D4: Delete without a valid session → 401.
- T-D5: Simulated failure mid-delete → full rollback.
- T-D6: Delete one branch → a second branch and its dependents remain intact.
- T-D7: Delete response contains no secret or internal identifier.

### Angular

- T-U1: Edit route is protected (guard) and loads the branch.
- T-U2: Form populates name/address/contact-details and all cameras.
- T-U3: Existing camera `cameraId`s are retained through edit and submission.
- T-U4: Add a camera → an extra camera row with no `cameraId` is submitted.
- T-U5: Edit a camera → the changed values are submitted with the retained `cameraId`.
- T-U6: Remove a camera → it is dropped from the submitted collection.
- T-U7: The final camera cannot be removed (its remove control is not offered).
- T-U8: The submitted payload matches the exact `PUT` contract.
- T-U9: Success navigates back to branch detail.
- T-U10: Validation failure blocks submission and shows field errors.
- T-U11: Backend failure shows a generic error and preserves entered values.
- T-U12: No Device/key/secret field is present in the edit view.
- T-U13: The list Edit icon links to the correct edit route and has an accessible label with the branch name.
- T-U14: The detail Edit action links to the correct edit route.
- T-U15: A Delete icon appears beside every branch with an accessible label containing the branch name.
- T-U16: Delete controls are keyboard operable.
- T-U17: Selecting Delete opens the confirmation; Cancel sends no request.
- T-U18: Confirming Delete calls the exact endpoint once; a duplicate click is prevented.
- T-U19: Successful deletion returns to the branch list and the branch no longer appears after reload.
- T-U20: The detail Delete action works the same way.
- T-U21: A 401 during delete is handled by existing session-expiry handling.
- T-U22: A 404 during delete yields a safe not-found result.
- T-U23: A server/network failure yields a generic error, never raw exception text.
- T-U24: No secret or internal identifier is rendered anywhere in edit or delete UI.
- T-U25: The existing T-23–T-29 tests remain green (no regression).

## 15. Out of Scope

- Soft deletion, tombstoning, audit trails, and retention policy (§8) — recorded as future needs, not implemented.
- Any remote Agent cleanup, notification, or wipe on delete (§7.4).
- Any change to the Device identity model, activation, reactivation, or activation-key generation/regeneration.
- Any Agent (real or simulated) or IP-02 work.
- Alerts, events, reports, health records, or any entity not in the delivered model.
- Bulk edit/delete, undo, or branch archival.

## 16. Traceability Matrix

| Requirement/Decision | Realized In This Feature |
|----------------------|--------------------------|
| FR-BRN-001 (branch/camera fields) | §4, §5.1, §5.2, §10.1, AC-1–AC-4 |
| FR-BRN-007 (persistent device identity) | §5.3, §7.1, AC-7 |
| ARCH-001 §13.1 (one or more cameras) | §4 (add/remove), §5.2, AC-3–AC-5 |
| FS-02 §12 (≥1 camera) | §4 #8, §6, AC-5 |
| ADR-015 (identity retained, secret rotates only on activation) | §5.3, §12, AC-7, AC-8 |
| ADR-013 (session) | §3, §7, AC-13 |
| ADR-009 (envelope) | §10 |
| NFR-SEC-001 (auth) | §3, §7.3, §12, AC-13 |
| ARCH-001 §15.6 / FS-02 §11 (no secrets/values in logs or errors) | §6, §12, AC-14 |
| ARCH-001 §10.5 (Branch Management module) | §9, AC-16 |
| NFR-USB-001 (single-view actions) | §9, §9.1, AC-16 |
| CON-007 (single device per branch) | §5.5, §5.6 (finite dependent set) |

---

*FS-03 — Branch & Camera Management is APPROVED (per explicit user approval of scope). It adds no SRS or ARCH-001 requirement; it manages the existing FS-02 model.*
