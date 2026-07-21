# Implementation Plan: Device Reactivation Security (Immediate Credential Revocation)

| Field | Value |
|-------|-------|
| Plan ID | IP-05 |
| Title | Device Reactivation Security — immediate shared-secret revocation on key regeneration |
| Status | Draft — awaiting approval |
| Milestone | Workstream A (continued) — credential lifecycle hardening over the delivered IP-01/IP-02 model |
| Realizes | FS-02 §5.3, §5.4, §5.8, §6, §7, §9, §10.2, §10.3, §11, §14 (AC-5, AC-17, AC-18) — reactivation-security amendment; SRS FR-BRN-005 / NFR-SEC-002 (amended); ARCH-001 §16.1/§16.4 / ADR-015 (amended) |
| Governing Documents | SRS-001 (amended for this change), ARCH-001 (amended for this change), FS-02 (amended for this change), Engineering Principles, Development Workflow |
| Depends On | IP-01 (T-01–T-30, complete), IP-02 (T-31–T-40 complete; T-41 in progress — Jetson deployment). Uses the delivered Backend, Angular, and Agent as-is. |
| Task ID Range | **T-48 – T-58** (T-31–T-41 reserved by IP-02; T-42–T-47 used by IP-03) |
| Owner | Farhan Naeem |
| Explicitly Excluded | Heartbeat / health monitoring / Online-Offline connectivity status (§14 of the brief — Offline stays reserved); DeepStream, detection, alerts, commands, siren, WebRTC; any new authenticated device endpoint; deleting/regenerating the Device ID or Device record; any change to Branch/Camera/Alert/audit relationships; any new entity; automatic activation retries; any `.claude/settings.json` or `.mcp.json` change. |

The governing-document amendments for this change were made first (spec-driven order): FS-02 (Amendments row + §5.3/§5.4/§5.8/§6/§7/§9/§10.2/§10.3/§11/§14), SRS FR-BRN-005 / NFR-SEC-002, and ARCH-001 §16.1/§16.4 / ADR-015. This plan implements exactly that amended behavior and nothing beyond it.

---

## 1. Problem and Objective

**Today** (verified in the delivered code — `DeviceService.RegenerateActivationKeyAsync`): regenerating a branch's Activation Key invalidates the old key record and mints a new one, but **deliberately does not touch the Device row** — the code comment reads *"regeneration does not change activation status"*. The device's `ProtectedSharedSecret` stays valid and `ActivationStatus` stays `Activated` until the Agent later reactivates. Between regeneration and reactivation the old credential still authenticates.

**Required** (amended FS-02 §5.3): regenerating the key of an **already-activated** device is a deliberate, security-first credential reset. In one atomic transaction the Backend must invalidate the old key(s), mint one new key, **immediately revoke the current shared secret**, and set `ActivationStatus = ReactivationRequired` — preserving the permanent `DeviceId`, the Device record, and all relationships. The Dashboard must warn before this and then show *Reactivation required* (never *Activated*, never *Offline*). Reactivation with the new key returns the device to `Activated` with the same `DeviceId`.

## 2. Grounding in the Delivered Code

Every design choice was verified against delivered code, not assumed:

| Fact | Evidence | Consequence for this plan |
|------|----------|---------------------------|
| Status is a 2-value enum stored as its **string name** in `nvarchar(32)` | `DeviceActivationStatus`, `DeviceConfiguration` (`HasConversion<string>().HasMaxLength(32)`) | Adding `ReactivationRequired` (20 chars) needs **no column migration**. |
| `Device.ProtectedSharedSecret` is **nullable** | `DeviceConfiguration` (`IsRequired(false)`) | Revocation = set it to `null`; no schema change. |
| `Device.Activate()` sets `Activated`, assigns `DeviceId` only if null, rotates the protected secret | `Device.cs` | Reactivation `ReactivationRequired → Activated` already works with **no domain change** to `Activate()`. |
| Regeneration is already one transaction, invalidates all non-`Invalidated` keys, inserts one `Unconsumed` key | `DeviceService.RegenerateActivationKeyAsync` | Extend it to also revoke the secret + set status when the device is activated; add the concurrency guard. |
| `ActivationStatus` reaches the wire only via `.ToString()` | `DeviceSummaryDto`, `DeviceDetailResponseDto` | `ReactivationRequired` flows through with **no DTO shape change** — comments only. |
| Reactivation path (`ActivateAsync`) already retains `DeviceId` and rotates the secret | `DeviceService.ActivateAsync`, `Device.Activate` | AC-11/12/13/14 need **no service change** beyond confirming behavior from `ReactivationRequired`. |
| Activation serializes on `UPDLOCK` of the key row (AC-16) | `ActivateAsync` | Regeneration needs its **own** concurrency guard for AC-18. |
| `IX_ActivationKeys_DeviceRecordId_Status` is **not unique** | `ActivationKeyConfiguration`, migration `20260714031219` | A **new filtered unique index** is required for "at most one `Unconsumed` key per device" (AC-18). |
| Backend `Device` has **no** `ActivatedAt`/`LastActivatedAt` | `Device.cs` (grep confirms none in backend) | Those timestamps are **Agent-only** (SQLite `DeviceIdentity`). This plan adds no Backend timestamp; AC-12/AC-13's timestamp parts are Agent-side and already implemented (IP-02 T-36/T-38). |
| Frontend badge maps unknown status → `Unknown`; models type 2 states | `device-status-badge.ts`, `branch.models.ts` | Must add `ReactivationRequired` explicitly so it is **not** rendered as `Unknown`. |
| Frontend confirm text says *"not deactivated and keeps running"* and does **not** re-read after regenerate | `branch-detail.ts` | Both must change: destructive warning + re-read so the badge flips. |

### 2.1 Authentication-enforcement rule (forward-looking, binding)

There is **no authenticated device endpoint yet** — the Agent makes no operational Backend calls in the current milestones (IP-02). So "the old secret can never authenticate again" (AC-10) is realized as the **atomic clearing of the stored `ProtectedSharedSecret`**, verified at the credential-state level; there is no live auth path to reject against.

Revocation must **not** rely only on `ProtectedSharedSecret` becoming `NULL`. Every current or future device-authentication path must require **both**:

- `ActivationStatus == Activated`, **and**
- a present, valid protected shared secret.

Status is checked **first**, so a device in `ReactivationRequired` (or `Unactivated`) is rejected **even if** inconsistent/legacy data accidentally leaves a secret value present. Dashboard status derives from Backend credential state, **not** process connectivity. Recorded in FS-02 §11 and ARCH-001 §16.4; must not be overstated in tests or docs. A shared, testable authorization predicate (`Device.CanAuthenticate() => ActivationStatus == Activated && ProtectedSharedSecret is not null`) is introduced in T-48 so the rule has a single enforcement point the future operational-API feature will call.

## 3. Concurrency Design and Failure Contract (AC-18)

A **filtered unique index** `UNIQUE (DeviceRecordId) WHERE Status = 'Unconsumed'` (mirroring the existing filtered-unique `DeviceId` index) makes "at most one unused key per device" a **schema invariant**. Two concurrent regenerations cannot both commit an `Unconsumed` key: the competing `INSERT` of an `Unconsumed` row for the same device violates the index and that transaction rolls back.

**No last-writer-wins claim.** The implementation does **not** provide deterministic ordering, so the plan does **not** claim "the most recent request wins." The guaranteed rule is exactly:

1. **exactly one** concurrent request succeeds and commits a valid `Unconsumed` key;
2. every competing request **fails cleanly** (its transaction rolls back) — it must **not** return a plaintext key that was never committed;
3. after completion, **exactly one** valid `Unconsumed` key exists for the device.

**Failure response contract (losing request).** The losing transaction's unique-index violation surfaces as a `DbUpdateException`; `RegenerateActivationKeyAsync` catches it, rolls back, and returns a typed *conflict* result (distinct from the existing `null` = not-found). The API maps it to **`409 Conflict`** with the uniform envelope, `errorCode: ACTIVATION_KEY_REGENERATION_CONFLICT`, a generic message, and **no `data`/plaintext key**. It is a safe, idempotent-to-retry outcome: the Admin may simply retry, which will observe the device already reset and mint a fresh key. No bounded auto-retry is added on the server (kept simple and explicit); the client surfaces a retry affordance.

**Ordering within the transaction.** SQL Server enforces the unique index per statement, so the regeneration transaction must **invalidate the old `Unconsumed` key(s) and flush that change before inserting** the new `Unconsumed` key (two `SaveChanges` calls inside the one transaction), so the index never transiently holds two `Unconsumed` rows for the device. Implementation requirement of T-50; covered by T-51's concurrency test.

## 4. Status State Machine (amended)

```
Unactivated ──activate(valid key)──────────────▶ Activated
Activated ──admin regenerates key──────────────▶ ReactivationRequired   [atomic: revoke secret + invalidate old keys + mint new key]
ReactivationRequired ──activate(new key)───────▶ Activated               [retain DeviceId, issue new secret]
ReactivationRequired ──admin regenerates again─▶ ReactivationRequired    [idempotent: secret already null; supersede unused key]
Unactivated ──admin regenerates key────────────▶ Unactivated             [unchanged: no secret, no DeviceId]
```

### 4.1 Domain invariants (enforced in the entity, tested in T-48)

The `Device` entity must prevent invalid state/credential combinations wherever practical. The valid combinations are exactly:

| Status | `DeviceId` | `ProtectedSharedSecret` |
|--------|-----------|--------------------------|
| `Unactivated` | `null` | `null` |
| `Activated` | present | present |
| `ReactivationRequired` | present | `null` |

Consequences enforced by the entity:
- `Activate()` requires a non-empty protected secret and assigns `DeviceId` if unset → establishes `Activated ⇒ (DeviceId present ∧ secret present)`.
- `RequireReactivation()` is valid only when the device has a `DeviceId` (i.e., currently `Activated` or already `ReactivationRequired`); it sets status `ReactivationRequired` and clears the secret → establishes `ReactivationRequired ⇒ (DeviceId present ∧ secret null)`; it **throws** on an `Unactivated` device (a caller bug).
- No public mutator can produce `Activated` with a null secret, `ReactivationRequired`/`Activated` with a null `DeviceId`, or `Unactivated` with a non-null `DeviceId`/secret.
- The authorization predicate `CanAuthenticate()` (§2.1) returns true only for `Activated` **with** a present secret, so even a hypothetically inconsistent row cannot authenticate a non-`Activated` device.

## 5. Implementation Tasks (dependency order)

Each task is independently implementable, testable, and committable (Engineering Principle 3). Focused tests run per task; formatting/lint/type-check must pass; no task proceeds on a failing test; nothing is committed until verified; nothing is pushed.

---

**T-48 — Domain: `ReactivationRequired` status, revocation transition, invariants, auth predicate**
- Objective: Add `ReactivationRequired` to `DeviceActivationStatus`. Add `Device.RequireReactivation()` that, only for a device that has activated (has a `DeviceId`), atomically sets `ActivationStatus = ReactivationRequired` and clears `ProtectedSharedSecret` (revocation); idempotent from `ReactivationRequired`; throws `InvalidOperationException` on an `Unactivated` device (no secret to revoke — a caller bug). Add the authorization predicate `Device.CanAuthenticate()` (§2.1): `ActivationStatus == Activated && ProtectedSharedSecret is not null`. Enforce the §4.1 invariants in the entity wherever practical. Confirm `Activate()` already restores `Activated` from `ReactivationRequired` retaining `DeviceId` — no change needed there.
- Files: `Domain/DeviceActivationStatus.cs`, `Domain/Device.cs`.
- Serves: FS-02 §5.3, §9, §11; AC-2, AC-3, AC-4, AC-12, AC-14 (domain invariants, §4.1).
- Tests (unit): revoke transition clears secret + sets status, retains `DeviceId`; idempotent from `ReactivationRequired` (stays `ReactivationRequired`, `DeviceId` preserved, secret stays null — clarification #5); throws on `Unactivated`; `Activate()` from `ReactivationRequired` → `Activated`, same `DeviceId`, new secret; `CanAuthenticate()` true only for `Activated`+secret, false for `ReactivationRequired` **even if** a secret value is forced present (clarification #2), false for `Unactivated`; the three §4.1 invariant combinations hold and invalid ones are unreachable via public mutators. No secret in any exception message.
- Exclusions: no timestamp fields; no service/API/DB work.

**T-49 — Persistence: filtered unique index migration (one unused key per device), with pre-migration safety**
- Objective: Add a filtered unique index `UNIQUE(DeviceRecordId) WHERE Status='Unconsumed'` in `ActivationKeyConfiguration` and a new EF migration; keep the existing non-unique `(DeviceRecordId, Status)` index. No other schema change (status/secret columns already suffice).
- Migration safety (clarification #7): the index cannot be created if any device already has >1 `Unconsumed` key. The migration `Up()` runs a **pre-check first** — a `migrationBuilder.Sql` guard that `THROW`s a clear error (naming the offending `DeviceRecordId` count, never any key material) if any `DeviceRecordId` has more than one `Unconsumed` key — then creates the index. This fails loudly rather than silently dropping keys. (In this prototype the app already maintains the single-unused-key invariant, so the guard is expected to be a no-op; the deterministic-cleanup alternative — keep one, invalidate the rest per a documented tie-break — is **not** used here because failing clearly is safer for a prototype with throwaway DBs, and no arbitrary key is ever silently chosen.)
- Files: `Infrastructure/Persistence/Configurations/ActivationKeyConfiguration.cs`, new `Migrations/*_ActivationKeyUnconsumedUniqueIndex.cs`, model snapshot.
- Serves: AC-6, AC-17 (structural invariant for AC-18).
- Tests (real SQL Server): a second `Unconsumed` key insert for the same device is rejected; an `Invalidated`/`Consumed` key alongside one `Unconsumed` is allowed; the pre-check guard raises clearly when a duplicate is seeded before the index exists; `has-pending-model-changes` clean after the migration; the migration applies cleanly against a clean throwaway DB. (Extends `DeviceActivationKeySchemaSqlServerTests`.)
- Exclusions: no automatic destructive backfill; no arbitrary key selection.

**T-50 — Service: security-first regeneration (revoke + reset atomically)**
- Objective: Extend `RegenerateActivationKeyAsync` so that, in the one transaction (ordering per §3):
  1. invalidate all non-`Invalidated` keys for the device **and flush** (`SaveChanges`) so the filtered unique index sees zero `Unconsumed` rows;
  2. if the device is currently `Activated` **or** `ReactivationRequired`, call `Device.RequireReactivation()` (revoke secret + set/keep `ReactivationRequired`, `DeviceId` preserved). An `Unactivated` device is left `Unactivated` with `DeviceId`/secret `null` (clarification #4);
  3. insert the new `Unconsumed` key and `SaveChanges`;
  4. `Commit`.
  **Plaintext-return safety (clarification #6):** the new plaintext key is returned **only after** `CommitAsync` succeeds. A rollback, a unique-index conflict, or any exception returns **no** key. A unique-index `DbUpdateException` is caught → rolled back → returned as a typed **conflict** result (distinct from `null` = not-found), which the controller maps to `409 ACTIVATION_KEY_REGENERATION_CONFLICT` (§3). The stale "regeneration does not change activation status" comment is removed.
- Files: `Infrastructure/Services/DeviceService.cs`; `IDeviceService.cs` (doc + the new conflict result type).
- Serves: FS-02 §5.3, §6; AC-1, AC-2, AC-3, AC-4, AC-5, AC-7, AC-17, AC-19.
- Tests: unit + **real SQL Server** integration — activated device: secret null + `ReactivationRequired` + all prior keys `Invalidated` + exactly one `Unconsumed`; **reactivation-required device regenerated again**: `DeviceId` preserved, secret stays null, prior unused key invalidated, exactly one new `Unconsumed`, stays `ReactivationRequired` (clarification #5); **unactivated device**: unchanged (`Unactivated`, `DeviceId` null, secret null), old key invalidated, one new key (clarification #4); forced mid-transaction failure rolls back secret/status/keys and returns **no** key (AC-7, #6); Branch/Camera relationships intact (AC-19).
- Exclusions: no reactivation-path (`ActivateAsync`) change (that already retains `DeviceId` and rotates the secret — confirmed in T-51); no server-side auto-retry.

**T-51 — Real SQL Server integration: reactivation + concurrency**
- Objective: End-to-end Backend integration against real SQL Server — reactivation after a security reset, and concurrent regeneration.
- Files: `WeaponDetection.IntegrationTests/*` (extend `DeviceServiceRegenerationTests`, `DeviceServiceActivationTests`, `RegenerateActivationKeyApiTests`, `ActivateApiTests`; new `ConcurrentRegenerationTests`).
- Serves: AC-10, AC-11, AC-12, AC-13, AC-14, AC-15, AC-16, AC-17, AC-18.
- Tests: after reset, activating with the new key → `Activated`, same `DeviceId`, new secret (AC-11/12/13/14); the superseded/old key → uniform `401 INVALID_ACTIVATION_KEY` (AC-15/16); the pre-reset secret value is gone from storage (AC-10, credential-state level, asserted as a boolean — never printed); **concurrent regeneration** leaves exactly one `Unconsumed` key (AC-18); activation (AC-1 path) still works unchanged.
- Exclusions: no auth-endpoint test (none exists — §2.1).

**T-52 — API/contracts: surface `ReactivationRequired` + the 409 conflict response**
- Objective: Confirm and document that `GET /api/v1/devices/{id}`, `GET /api/v1/branches`, `GET /api/v1/branches/{id}` return `ReactivationRequired`; the successful regenerate response is unchanged (one-time key only). Map the new regeneration **conflict** result (from T-50) to **`409 Conflict`** with the uniform envelope, `errorCode: ACTIVATION_KEY_REGENERATION_CONFLICT`, and no `data` (§3). Update the now-inaccurate DTO/interface comments ("always Activated", "two states"). No success-path DTO shape change.
- Files: `Api/Controllers/DeviceController.cs` (409 mapping), `DeviceDetailResponseDto.cs`, `BranchResponseDto.cs`, `IDeviceService.cs` (comments); `WeaponDetection.IntegrationTests` API tests.
- Serves: FS-02 §5.4, §10.2, §10.3; AC-9 (Backend side), AC-18 (loser returns no key).
- Tests: API returns `"ReactivationRequired"` for a reset device with its retained `deviceId`; successful regenerate response body unchanged; the conflict path returns `409` + the code + **no key/data**; no secret/hash/`DeviceRecordId` on any path.

**T-53 — Angular: model + status badge**
- Objective: Add `'ReactivationRequired'` to the `DeviceActivationStatus` type and render it in `device-status-badge` as **"Reactivation required"** with its own description and style modifier — explicitly a known state, never `Unknown`, never `Offline`.
- Files: `frontend/src/app/branches/branch.models.ts`, `device-status-badge.ts`, `device-status-badge.spec.ts`.
- Serves: FS-02 §7; AC-9.
- Tests: badge renders the label/description for `ReactivationRequired`; still maps a truly unknown value to `Unknown`; never labels it Offline.

**T-54 — Angular: destructive warning, confirmation, and post-reset refresh**
- Objective: Show the required destructive security warning ("Generating a new Activation Key will immediately revoke this device's current credentials. The device will be unable to authenticate until the new key is securely provisioned on the Jetson.") **only when the device is currently `Activated` or `ReactivationRequired`** (clarification #4); for an `Unactivated` device, a plain confirmation without the revocation warning. Keep the explicit confirm. After a successful regeneration that reset an activated/reactivation-required device, **re-read the branch** so the badge flips to *Reactivation required* (the current code deliberately does not refresh). The device-id line still shows the retained `DeviceId`. Surface the `409` conflict outcome as a benign "another change was in progress — try again" retry affordance, showing **no** key.
- Files: `frontend/src/app/branches/branch-detail.ts`, `branch-detail.spec.ts`.
- Serves: FS-02 §7; AC-8, AC-9, AC-18 (client shows no key on conflict).
- Tests: destructive warning present for `Activated`/`ReactivationRequired`, absent for `Unactivated`; cancel sends nothing; confirm calls the endpoint once; on success the branch is re-fetched and status shows *Reactivation required* not *Activated*/*Offline*; the one-time key is displayed once; a `409` shows the retry affordance and no key.

**T-55 — Agent: simulated-Backend contract tests (reactivation)** *(distinct task from T-56)*
- Objective: In the Agent's simulated-Backend suite, extend the reactivation scenario to reflect that the Backend revokes at regeneration; assert the Agent still makes exactly one `POST /api/v1/activate`, retains `DeviceId`, rotates its local secret, preserves `ActivatedAt`, advances `LastActivatedAt`, deletes the key file only after persistence, never retries, and logs no secret. (No Agent production change expected — the Agent contract is unchanged.)
- Files: `agent/tests/integration/*`.
- Serves: AC-11, AC-12, AC-13, AC-20; MAC-9 secret safety.
- Exclusions: no Agent production-code change unless a test proves one is needed.

**T-56 — Agent: real-Backend contract tests (reactivation)** *(distinct task from T-55)*
- Objective: In the opt-in real-Backend contract suite, drive the amended flow against the actual Backend: activate; **regenerate** (device becomes `ReactivationRequired`, secret revoked, old key `401`); reactivate with the new key (same `DeviceId`, new secret, back to `Activated`); assert the superseded key gets the uniform `401`.
- Files: `agent/tests/contract/*`, `agent/tests/support/*`.
- Serves: AC-10, AC-11, AC-14, AC-15, AC-16, AC-19.
- Exclusions: no Backend production change to make a test pass (the real regenerate/activate endpoints already exist).

**T-57 — Real Jetson reactivation verification (extends IP-02 MAC-12)**
- Objective: On the real Jetson (continuing T-41): with the device `Activated`, regenerate the key in the Dashboard → confirm the Dashboard shows **Reactivation required** and the Backend has revoked the secret; provision the new key with `set-activation-key.sh`; restart the service → confirm reactivation, same `DeviceId`, `Activated` again, `LastActivatedAt` advanced, key file consumed, no secret in journal.
- Files: none (verification); evidence recorded in docs (T-58).
- Serves: AC-9, AC-11, AC-12, AC-13, AC-14, AC-20 on real hardware.
- Exclusions: no reboot/DeepStream scope beyond MAC-12; pause for the user to generate the key in the UI.

**T-58 — Documentation and evidence**
- Objective: Record the delivered behavior and evidence: root `README.md` (security notes, known limitations), `agent/README.md` and `deployment/jetson/README.md` (reactivation now shows *Reactivation required*), this plan's status, and the AC-1–AC-20 evidence table. Do not include any key or secret.
- Files: `README.md`, `agent/README.md`, `deployment/jetson/README.md`, `specs/implementation-plans/IP-05-device-reactivation-security.md`.
- Serves: Engineering Principle 10; AC-18 (no secrets committed).

## 6. Acceptance-Criteria → Task Traceability

| AC | Summary | Task(s) |
|----|---------|---------|
| AC-1 | First activation still works | T-50, T-51 |
| AC-2 | Regeneration preserves `DeviceId` | T-48, T-50, T-51 |
| AC-3 | Regeneration immediately revokes the secret | T-48, T-50 |
| AC-4 | Status atomically `Activated → ReactivationRequired` | T-48, T-50 |
| AC-5 | All previously unused keys invalidated | T-50, T-51 |
| AC-6 | Only one unused valid key after regeneration | T-49, T-50 |
| AC-7 | Failed regeneration leaves prior state unchanged | T-50, T-51 |
| AC-8 | Dashboard shows destructive warning first | T-54 |
| AC-9 | Dashboard shows *Reactivation required* (not Activated/Offline) | T-52, T-53, T-54 |
| AC-10 | Old secret never authenticates again (credential-state level, §2.1) | T-50, T-51, T-56 |
| AC-11 | New key reactivates exactly once | T-51, T-56 |
| AC-12 | Reactivation retains `DeviceId` and original `ActivatedAt` (Agent-side) | T-51, T-55, T-56 |
| AC-13 | Reactivation rotates secret and advances `LastActivatedAt` (Agent-side) | T-51, T-55 |
| AC-14 | Successful reactivation returns to `Activated` | T-51, T-52, T-56 |
| AC-15 | Consumed key → `401 INVALID_ACTIVATION_KEY` | T-51, T-56 |
| AC-16 | Superseded key → same uniform `401` | T-51, T-56 |
| AC-17 | Concurrent regeneration cannot leave multiple valid keys | T-49, T-51 |
| AC-18 | No key/secret in logs, exceptions, URLs, argv, or committed files | all tasks (secret-safety), T-58 |
| AC-19 | Branch/Camera/historical records stay linked to the same `DeviceId` | T-50, T-51, T-56 |
| AC-20 | Normal restart/reboot after reactivation loads identity without re-activating | T-55, T-57 |

## 7. Non-Goals / Constraints

- **No** heartbeat, health monitoring, or Online/Offline status (Offline stays reserved for future connectivity — brief §14).
- **No** DeepStream, detection, alerts, commands, siren, WebRTC.
- **No** new authenticated device endpoint; revocation is credential-state only (§2.1).
- **No** deletion/regeneration of the `DeviceId` or Device record; **no** relationship removal.
- **No** automatic activation retries (IP-02 §14 preserved).
- Do **not** push to any remote; do **not** modify `.claude/settings.json` or `.mcp.json`.

## 8. Open Items

- **OI-A (documented, not blocking):** with no authenticated device endpoint yet, AC-10 is verified at the credential-state level, not by a rejected authenticated call. When the operational Agent API arrives, its auth check must read `ProtectedSharedSecret` state so a revoked device is rejected. Recorded in FS-02 §11 and ARCH-001 §16.4.

---

*IP-05 — Device Reactivation Security. Status: Draft, awaiting approval. Governing-document amendments (FS-02, SRS, ARCH-001/ADR-015) have been made; task implementation (T-48–T-58) has not started.*
