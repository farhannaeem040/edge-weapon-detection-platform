# Implementation Plan: Branch & Camera Management (Edit / Delete)

| Field | Value |
|-------|-------|
| Plan ID | IP-03 |
| Title | Branch & Camera Management (Edit / Delete) |
| Status | Approved |
| Milestone | Workstream A (continued) — Branch/Camera edit and delete over the delivered IP-01 model |
| Realizes | FS-03 (all acceptance criteria) |
| Feature Specification Links | [`specs/features/FS-03-branch-camera-management.md`](../features/FS-03-branch-camera-management.md) |
| Governing Documents | SRS-001 (frozen), ARCH-001 (Final), FS-01, FS-02 (Final), Engineering Principles |
| Depends On | IP-01 (T-01–T-30, complete). Independent of IP-02 (Jetson Agent); touches no Agent code. |
| Task ID Range | **T-42 – T-47** (T-31–T-41 are reserved by IP-02 and are not used here) |
| Owner | Farhan Naeem |
| Explicitly Excluded | Any Agent/IP-02 work; soft delete/audit/retention; remote Agent cleanup; changes to activation, reactivation, activation-key generation/regeneration, or the Device identity model; any new entity; any SRS/ARCH-001 change; any EF schema migration (the existing schema already supports edit and delete). |

This plan does not modify SRS-001, ARCH-001, FS-01, or FS-02. It breaks approved FS-03 behavior into six independently committable tasks within the structure ARCH-001 §10.1/§10.5 already fixes for the Backend and the Angular Branch Management module.

---

## 1. Grounding in the Delivered Code

Every design choice below was verified against the code delivered by IP-01, not assumed:

| Fact | Evidence |
|------|----------|
| `CameraId` (external GUID) is already public | `CameraResponseDto.CameraId`; Angular `Camera.cameraId`. **Reused as the stable camera identifier for edits — no new identifier is added** (FS-03 §1.3). |
| `DeviceRecordId` is internal-only | Absent from every DTO and Angular model. Never exposed by this plan. |
| Camera → Branch FK is `OnDelete(Cascade)` | `CameraConfiguration`. Documented but untested (no delete path existed). |
| Device → Branch FK is `OnDelete(Cascade)` | `DeviceConfiguration`. Same. |
| ActivationKey → Device FK is `OnDelete(Cascade)` | `ActivationKeyConfiguration`. Same. |
| The uniform envelope is applied centrally | `ApiResponse`, `ApiEnvelopeResultFilter`. Reused, not re-implemented. |
| A DTO-valid-but-business-invalid request maps to 400 | `BranchController.Create` catches `ArgumentException` → `BadRequest(ApiResponse.Fail("VALIDATION_ERROR", ...))`. This plan follows that exact convention. |
| RTSP validation lives in `BranchService.EnsureValidRtspUrl` | Reused for the update path (extracted to a shared helper, not duplicated). |
| RTSP URLs are redacted on the wire by `RtspUrlSanitizer` | Reused by the update response, which uses the same `BranchResponseDto.ForRead` shape. |
| Auth is a fallback policy; controllers opt out with `[AllowAnonymous]` | `BranchController` carries none, so `PUT`/`DELETE` added to it are automatically protected (ADR-013). |

### 1.1 Deletion Order Decision

The three cascade rules above would let a single `DELETE FROM Branches` cascade to Cameras, Device, and (via Device) Activation Keys. However, those cascades are **documented but not tested** (each config comment says "No delete endpoint exists yet; this declares the schema-level behavior for when one does"). FS-03 §11 and the approved T-44 task both call for an **explicit, ordered, transactional delete**. Therefore T-44 deletes explicitly — Activation Keys, then Device, then Cameras, then Branch — inside one transaction. This makes the guarantee independent of cascade configuration, makes it directly testable, and is robust even if a future migration changes a cascade rule. No schema change is made.

## 2. No Migration Required

The existing schema (M1–M3) already models everything edit and delete need: Branch/Camera/Device/ActivationKey tables, the FKs, and the constraints. Editing updates existing rows and inserts/deletes Camera rows; deleting removes rows. **No new column, table, index, or constraint is introduced, so no EF migration is authored.** T-47 verifies this with `dotnet ef migrations has-pending-model-changes` (expected: none).

## 3. Backend Design

### 3.1 Application Layer (`IBranchService`)

Two methods are added to the existing interface (mirroring the existing `CreateBranchAsync`/read methods):

```
Task<BranchUpdateResult> UpdateBranchAsync(UpdateBranchRequest request, CancellationToken)
Task<BranchDeletionOutcome> DeleteBranchAsync(Guid branchId, CancellationToken)
```

New Application-layer records (independent of API DTOs and EF types, matching the existing `NewBranchRequest` precedent):

- `UpdateBranchRequest(Guid BranchId, string Name, string Address, string ContactDetails, IReadOnlyList<CameraMutation> Cameras)`
- `CameraMutation(Guid? CameraId, string Name, string RtspUrl)` — `CameraId` null ⇒ add; non-null ⇒ update existing.
- `BranchUpdateResult` — a discriminated outcome: `Updated(BranchView)`, `NotFound`, or `Invalid` (so the API layer maps to 200/404/400 without exceptions-as-flow, while still tolerating the existing `ArgumentException` convention for Domain-level guards).
- `BranchDeletionOutcome` — `Deleted` or `NotFound`.

Rationale for an explicit result type (vs. the create path's throw-on-invalid): update has a genuine, expected **404** (unknown branch) and a genuine **business-validation 400** (foreign/duplicate `cameraId`) that are normal outcomes, not bypassed-gate exceptions. Modelling them as results keeps them first-class and testable, consistent with how `DeviceActivationResult` and `RegenerateActivationKeyAsync`'s nullable result already treat expected non-success outcomes.

### 3.2 Update Algorithm (one transaction)

Implemented in `BranchService.UpdateBranchAsync`, tracked (not `AsNoTracking`, because it mutates):

1. Begin transaction.
2. Load the Branch by `BranchId`; null ⇒ rollback, return `NotFound`.
3. Validate branch scalar fields (reuse Domain length rules) and the "≥1 camera" rule; invalid ⇒ return `Invalid`.
4. Load the branch's Cameras (tracked).
5. Validate each request camera's name and RTSP URL (reuse the shared RTSP helper).
6. Reject duplicate `cameraId`s within the request ⇒ `Invalid`.
7. Reject any non-null `cameraId` that is not among the branch's loaded cameras (unknown or foreign) ⇒ `Invalid`.
8. Apply branch scalar updates via a Domain mutator (§3.4).
9. For each request camera with a `cameraId`: update the matched tracked Camera in place (name, RTSP URL) via a Domain mutator; `cameraId` preserved.
10. For each request camera without a `cameraId`: construct a new `Camera(branchId, name, rtspUrl)` and add it.
11. For each loaded Camera whose `cameraId` is absent from the request: remove it.
12. `SaveChanges`; commit. On `DbUpdateException`: rollback, wrap in `InvalidOperationException` with a message that never echoes any value (existing precedent).
13. The Device and Activation Key rows are never touched by any step above.
14. Return `Updated` with the re-projected `BranchView`.

### 3.3 Delete Algorithm (one transaction)

Implemented in `BranchService.DeleteBranchAsync`:

1. Begin transaction.
2. Load the Branch by `BranchId`; null ⇒ rollback, return `NotFound`.
3. Load the branch's Device (single) and its Activation Keys (by `DeviceRecordId`), and its Cameras.
4. Remove Activation Keys.
5. Remove the Device.
6. Remove the Cameras.
7. Remove the Branch.
8. `SaveChanges`; commit. On failure: rollback, wrap without echoing values.
9. Return `Deleted`.

### 3.4 Domain Mutators

The entities currently have private setters and no mutators (they were create-only). This plan adds **minimal, invariant-preserving** mutators — it does not loosen encapsulation:

- `Branch.UpdateDetails(name, address, contactDetails)` — same `Require` validation the constructor uses; `BranchId` untouched.
- `Camera.UpdateConfiguration(name, rtspUrl)` — same presence/length validation the constructor uses; `CameraId`, `BranchId`, `Enabled` untouched; the value is never interpolated into an exception (existing rule).

These keep validation in the Domain where the constructors already put it, so the DTO/service and the entity cannot drift.

### 3.5 API Layer

`BranchController` gains two actions (it already carries no `[AllowAnonymous]`, so both are protected by the fallback policy — ADR-013):

- `PUT /api/v1/branches/{branchId:guid}` — binds `UpdateBranchRequestDto`; `[ApiController]` DataAnnotations reject blank/oversized fields and an empty camera list as 400 before the action runs; the action maps `BranchUpdateResult` → 200 (safe read DTO) / 404 / 400.
- `DELETE /api/v1/branches/{branchId:guid}` — maps `BranchDeletionOutcome` → 200 (bare success envelope) / 404.

New API DTOs (in `Contracts`):

- `UpdateBranchRequestDto(Name, Address, ContactDetails, IReadOnlyList<UpdateCameraDto> Cameras)` with the same DataAnnotations as `CreateBranchRequestDto`.
- `UpdateCameraDto(Guid? CameraId, Name, RtspUrl)` — `CameraId` optional; name/RTSP annotated like `CameraConfigDto`.
- The **update response reuses `BranchResponseDto.ForRead`** — identical safe shape to the read endpoints, so RTSP redaction and the no-key/no-secret/no-`DeviceRecordId` guarantees come for free.

## 4. Angular Design

### 4.1 Service (`BranchService`)

Two methods added, mirroring the existing `create`/`get` envelope-unwrapping style:

- `update(branchId, request: UpdateBranchRequest): Observable<Branch>` — `PUT`, unwrap, return the safe branch; 404 → `null`-style handling consistent with `get` is **not** used here (an edit of a branch the user is on is a failure, not a normal empty state) — instead 404 propagates to the generic error, matching how a failed edit should read.
- `delete(branchId): Observable<'deleted' | 'not-found'>` — `DELETE`; map 200 → `'deleted'`, 404 → `'not-found'` (a documented outcome, like `get`/`regenerate`), propagate everything else.

New model types in `branch.models.ts`:

- `UpdateCameraRequest { cameraId?: string; name: string; rtspUrl: string }`
- `UpdateBranchRequest { name: string; address: string; contactDetails: string; cameras: UpdateCameraRequest[] }`

### 4.2 Edit Form (`BranchEditComponent`, route `/branches/:branchId/edit`)

- Loads the branch via `BranchService.get`; on 404/null → a safe not-found state; on failure → generic error.
- Builds a reactive form pre-populated with name/address/contact-details and a cameras `FormArray`, one group per existing camera carrying a **hidden `cameraId` control** plus name/rtspUrl.
- Reuses `notBlank`/`rtspUrl` validators and the `CameraConfigFormComponent` for each row (extended so an added row has an empty/absent `cameraId`). Added rows carry no `cameraId`; removed rows are dropped from the array; the last remaining row's remove control is not offered (reusing the existing `removable` input pattern).
- Submits the exact `PUT` payload (each camera → `{ cameraId?, name, rtspUrl }`, trimmed), guards against double submission, navigates to branch detail on success, shows one generic error and preserves entered values on failure.
- Renders **no** Device, activation-key, secret, or `DeviceRecordId` field.

### 4.3 Icons and Actions

- A small shared inline-SVG icon set (pencil, trash) — no icon library is installed for two glyphs (FS-03 §9). Implemented as tiny standalone components or inline templates with `aria-hidden="true"` on the SVG and the accessible name on the control.
- **Branch list:** each row gains an Edit link (pencil) to `/branches/:branchId/edit` and a Delete button (trash) opening the confirmation, grouped together beside the row, with `aria-label`s including the branch name. The branch name link is unchanged and is never the delete control.
- **Branch detail:** an Edit action (to the edit route) and a Delete action (opening the confirmation), placed in the header actions area.

### 4.4 Delete Confirmation

- An accessible confirmation panel/dialog (`role="group"`/dialog semantics, labelled heading — following the existing regeneration-confirmation pattern in `branch-detail.ts`) naming the branch, warning it cannot be undone, listing that cameras/device registration/activation keys are deleted and that a physical Agent is not remotely wiped.
- Cancel closes it and sends nothing; Delete calls `BranchService.delete` once, is disabled while in flight, and on success navigates to `/branches`. 401 is handled by the existing session-expiry interceptor; 404 → safe not-found feedback; other failures → generic error. No deleted credentials are ever shown.

## 5. Implementation Tasks

Each task is independently committable (Engineering Principle 3) and lists its FS-03 acceptance criteria.

---

**T-42 — Branch and Camera update application service**
- Objective: Implement the transactional update in the Application/Infrastructure layers (§3.1–§3.4): `IBranchService.UpdateBranchAsync`, the request/result records, the `Branch.UpdateDetails`/`Camera.UpdateConfiguration` Domain mutators, and the shared RTSP-validation helper (extracted from the existing private method, not duplicated).
- Dependencies: IP-01 (delivered). None within IP-03.
- Files/Components: `Domain/Branch.cs`, `Domain/Camera.cs`, `Application/Interfaces/IBranchService.cs`, `Infrastructure/Services/BranchService.cs`
- Expected Output: One transactional method applying branch-field updates and full camera reconciliation (update-in-place / add / remove), preserving Device and Activation Key rows, returning a typed `Updated`/`NotFound`/`Invalid` result.
- Tests: Integration (real SQL Server, per IP-01 §9) — FS-03 T-A1–T-A17: scalar update; existing-camera update preserving `cameraId`; add one / add multiple; remove one; multi-change in one transaction; last-camera-removal rejected; unknown branch; foreign `cameraId`; duplicate `cameraId`; invalid RTSP; rollback on simulated failure; `DeviceRecordId`/`DeviceId`/status/`ProtectedSharedSecret`/Activation-Key-records all unchanged. Plus unit tests for the Domain mutators.
- Completion Evidence: Integration + unit tests pass against real SQL Server.
- Serves: AC-1–AC-9.
- Exclusions: No API endpoint (T-43); no delete (T-44); no Device/key mutation; no schema change.
- Documentation Update: none yet (documented at T-47).
- Commit: `feat(branches): add transactional branch update service`

**T-43 — Branch update API endpoint**
- Objective: Expose `PUT /api/v1/branches/{branchId:guid}` (§3.5): `UpdateBranchRequestDto`/`UpdateCameraDto`, the controller action mapping the service result to 200 (safe read DTO) / 400 / 404, protected by the existing fallback auth policy.
- Dependencies: T-42.
- Files/Components: `Api/Contracts/UpdateBranchRequestDto.cs`, `Api/Controllers/BranchController.cs` (extend)
- Expected Output: The full route works over HTTP with the standard envelope; response is the safe read shape (no key, no secret, no `DeviceRecordId`); RTSP redacted.
- Tests: Integration (real SQL Server) — full route, authorization (401 unauthenticated/invalid session), exact success payload, 400 validation cases, 404 unknown branch, foreign/duplicate `cameraId` rejection, and the preservation rules re-asserted over HTTP (FS-03 T-A1–T-A11, T-A18; AC-13, AC-14).
- Completion Evidence: Integration tests pass; manual `curl`/Postman edit succeeds.
- Serves: AC-1–AC-9, AC-13, AC-14, AC-17 (backend side).
- Exclusions: No delete; no Angular; no return of any secret/entity.
- Documentation Update: none yet.
- Commit: `feat(branches): add branch update endpoint`

**T-44 — Transactional Branch deletion service and API**
- Objective: Implement `IBranchService.DeleteBranchAsync` (§3.3, explicit ordered delete in one transaction) and expose `DELETE /api/v1/branches/{branchId:guid}` mapping to 200 (bare success envelope) / 404, protected by the fallback policy.
- Dependencies: T-42 (shares `IBranchService`); independent of T-43.
- Files/Components: `Application/Interfaces/IBranchService.cs` (extend), `Infrastructure/Services/BranchService.cs` (extend), `Api/Controllers/BranchController.cs` (extend)
- Expected Output: Deleting a branch removes its Activation Keys, Device, Cameras, and the Branch atomically, for both activated and unactivated branches; no entity data or credential is returned.
- Tests: Integration (real SQL Server) — FS-03 T-D1–T-D7: delete unactivated; delete activated; each dependent removed; unknown branch 404; unauthorized 401; rollback on simulated failure; another branch unaffected; response carries no secret/internal id. Include an assertion that after deleting an activated branch, no Device row with that `DeviceId` remains (AC-12 — credentials no longer recognisable).
- Completion Evidence: Integration tests pass.
- Serves: AC-10, AC-11, AC-12, AC-13, AC-14.
- Exclusions: No remote Agent contact (FS-03 §7.4); no soft delete; no Angular.
- Documentation Update: none yet.
- Commit: `feat(branches): add branch deletion endpoint`

**T-45 — Angular Branch edit form + Edit icons**
- Objective: Implement the protected `/branches/:branchId/edit` route and `BranchEditComponent` (§4.2), the `update` service method and update models (§4.1), and the accessible Edit icon on the branch list and the Edit action on branch detail (§4.3).
- Dependencies: T-43 (consumes the endpoint).
- Files/Components: `frontend/src/app/branches/branch-edit.ts` (+ spec), `branch.models.ts` (extend), `branch.service.ts` (extend `update`), `branch.routes.ts` (add edit route), `branch-list.ts` / `branch-detail.ts` (add Edit control), a shared pencil-icon (inline SVG), `branch.service.spec.ts` (extend)
- Expected Output: An Admin can edit a branch and its cameras through the UI, preserving existing `cameraId`s, adding/removing cameras (≥1 remaining), submitting the exact `PUT` payload, and returning to detail on success.
- Tests: Angular unit — FS-03 T-U1–T-U14: route protection; load; population; `cameraId` retention; add/edit/remove camera; last-camera-removal prevented; exact payload; success navigation; validation failure; backend failure preserving values; no Device/key/secret field; list Edit icon route + accessible label; detail Edit action route.
- Completion Evidence: Angular unit tests pass; manual edit against a running Backend.
- Serves: AC-1–AC-4 (UI), AC-16 (Edit), AC-17.
- Exclusions: No delete UI (T-46); no icon library install.
- Documentation Update: none yet.
- Commit: `feat(frontend): add branch and camera editing`

**T-46 — Angular Branch delete confirmation and Branch action icons**
- Objective: Implement the Delete icon beside every branch (list) and the Delete action on detail, the accessible confirmation flow (§4.4), and the `delete` service method (§4.1). Group Edit and Delete together beside each branch row.
- Dependencies: T-44 (endpoint), T-45 (shares the list/detail action area and service).
- Files/Components: `branch.service.ts` (extend `delete`), `branch-list.ts` / `branch-detail.ts` (add Delete control + confirmation), a shared trash-icon (inline SVG), a confirmation component or inline panel, specs
- Expected Output: An Admin can delete a branch through an explicit confirmation; cancel sends nothing; confirm calls the endpoint once; success returns to the list and the branch is gone; 401/404/other handled safely; no credentials displayed.
- Tests: Angular unit — FS-03 T-U15–T-U25: Delete icon beside every branch; `aria-label` with branch context; keyboard operability; confirmation opens; Cancel sends no request; confirm calls the exact endpoint once; duplicate click prevented; success returns to list; deleted branch absent after reload; detail Delete action; 401 handling; 404 handling; generic server failure; no secret/internal-id rendering; Edit icon still works; **T-23–T-29 remain green**.
- Completion Evidence: Angular unit tests pass, including the no-regression check.
- Serves: AC-10 (UI trigger), AC-15, AC-16 (Delete), AC-17.
- Exclusions: No remote Agent wipe messaging beyond the accurate "not remotely wiped" note; no bulk delete.
- Documentation Update: none yet.
- Commit: `feat(frontend): add branch deletion actions`

**T-47 — Documentation and full verification**
- Objective: Update documentation and run the complete Backend + Angular verification and the manual script (FS-03 §14 companion). Confirm no migration was needed, no Agent/IP-02 work was added, no secrets/generated output committed.
- Dependencies: T-42–T-46.
- Files/Components: `README.md`, `frontend/README.md`, `frontend/src/app/branches/README.md`, `specs/features/FS-03-branch-camera-management.md` (status confirmation only), `specs/implementation-plans/IP-03-branch-camera-management.md` (this file)
- Expected Output: Green Backend suite (real SQL Server) and Angular suite; documented edit/delete capability, hard-delete prototype semantics, the routes, authorization, UI actions, confirmation flow, preservation guarantees, and the known future soft-delete/retention need; updated project status and API summary.
- Tests: Full-suite execution (no new tests authored); `dotnet ef migrations has-pending-model-changes` → none.
- Completion Evidence: Full-suite run report; `git diff --check` clean; no migration added; no `.claude/settings.json`/secrets/generated output staged.
- Serves: All FS-03 acceptance criteria (full-suite confirmation), Engineering Principle 10.
- Exclusions: No new feature; no T-48+ work.
- Documentation Update: as listed.
- Commit: `docs(branches): document branch edit and deletion`

## 6. Definition of Done

1. T-42–T-47 complete, each independently committed.
2. Every FS-03 acceptance criterion (AC-1–AC-17) demonstrated by a passing automated test.
3. The manual script (FS-03 §14 / IP-03 §5 T-47) runs end-to-end against a local Backend + Angular with no direct DB manipulation.
4. No secret, activation key, `DeviceRecordId`, or EF entity appears in any committed file, log, or response.
5. No EF migration was added (verified), because no schema change was required.
6. No IP-02/Agent code was touched.
7. Documentation updated (Engineering Principle 10); no claim of soft delete, audit retention, or remote Agent cleanup.
8. No SRS/ARCH-001/FS-01/FS-02 content modified.

## 7. Manual Verification Script (T-47)

Against a throwaway database:
1. Log in. 2. Create a branch with two cameras. 3. Confirm Edit and Delete icons appear beside it. 4. Edit branch fields. 5. Edit one camera. 6. Add one camera. 7. Remove another camera. 8. Confirm Device identity and activation status are unchanged. 9. Confirm no Activation Key was regenerated. 10. Open Delete, cancel, confirm nothing changed. 11. Delete the branch after confirmation. 12. Confirm Branch, Cameras, Device, and Activation Keys are gone. 13. Confirm another branch is unaffected. 14. Confirm protected routes require authentication. 15. Confirm no secrets appear in UI, logs, URLs, or screenshots. Delete throwaway data afterward.

---

*IP-03 — Branch & Camera Management. Status: Approved. Task IDs T-42–T-47 (T-31–T-41 reserved by IP-02).*
