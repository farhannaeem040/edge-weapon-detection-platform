# Feature Specification: Branch & Device Onboarding

| Field | Value |
|-------|-------|
| Feature ID | FS-02 |
| Title | Branch & Device Onboarding |
| Status | Final |
| Related SRS Requirements | FR-BRN-001, FR-BRN-002, FR-BRN-003, FR-BRN-004, FR-BRN-005, FR-BRN-006, FR-BRN-007, BR-002, BR-003, ASM-006, ASM-008, NFR-SEC-002 |
| Related Architecture Sections | ARCH-001 §10.1 (BranchController/DeviceController, BranchService/DeviceService), §13.1 (Branch, Camera, Device entities), §13.2 (DeviceIdentity, ConfigCache), §13.3 (Credential/Secret Storage), §14.1 (Backend API), §15.1/§15.5 (Trust Boundaries, Activation Key Storage), §16 (Device Lifecycle) |
| Related ADRs | ADR-002 (amended) — Security Architecture; ADR-004 — Local Persistence; ADR-008 — Filesystem Layout; ADR-015 — Device Reactivation Policy |
| Owner | Farhan Naeem |
| Dependencies | FS-01 (Admin must be authenticated to create/manage branches and regenerate Activation Keys via the Dashboard) |
| Supersedes | `specs/features/FS-02-branch-device-onboarding-activation.md` (restructured into implementation increments; this Final revision additionally corrects the Device identity model and the Activation Key format/lookup design — no SRS/ARCH-001 requirement changes) |

---

## 1. Purpose and Scope

This feature specifies the behavior required for a System Administrator to register a branch and its cameras, for the Backend to issue a one-time Activation Key for that branch's device, and for a Jetson Agent to exchange that key for a persistent Device identity and shared secret during activation. It also specifies Activation Key regeneration and the reactivation of a previously activated device.

This feature realizes FR-BRN-001–007, BR-002, BR-003, ASM-006, ASM-008, and NFR-SEC-002 as architected in ARCH-001 §16 (Device Lifecycle) and §15 (Security Architecture). It covers only the single-device-per-branch, Admin-initiated onboarding model established by the SRS and ARCH-001; it does not introduce device self-registration, multiple devices per branch, or remote zero-touch provisioning.

**Boundary with other features.** FS-02 covers branch/device onboarding and the activation/reactivation exchange only. Ongoing Agent→Backend operations that use the `X-Device-Id`/`X-Device-Secret` headers issued by this feature — heartbeat, alert/snapshot submission, offline-event sync, configuration retrieval — are specified in their own respective Feature Specifications and are referenced here only to describe what the activation exchange produces. Starting the DeepStream pipeline is part of Agent bootstrap and is covered by a later Feature Specification; FS-02 specifies only that activation returns the configuration the Agent will use for that startup.

### 1.1 Implementation Increments

This specification is structured for delivery in two increments, consistent with the project's Backend/Angular-first delivery strategy. The increments are a **delivery-order decision only** — they do not change requirements, behavior, or acceptance criteria. Every behavior specified in Increment B is already fully defined here; it is simply implemented after Increment A.

| Increment | Delivers | Consumer at implementation time |
|-----------|----------|-----------------------------------|
| **Increment A — Backend and Angular** | Branch/Camera/Device data model (including the internal/external identity split, §1.3), Activation Key generation and regeneration (including the two-part key format, §1.4), branch/device list and detail views, device activation-status display. The Backend also implements `POST /api/v1/activate` in full during Increment A, since it is a Backend-only endpoint with no Angular surface. | Admin, via the Dashboard, immediately. |
| **Increment B — Agent activation** | The client-side behavior of exchanging an Activation Key for a persistent Device identity, shared secret, and configuration, and persisting that identity locally; reactivation behavior from the Agent's perspective. | The real FastAPI Jetson Agent, and — before that — a simulated Jetson client exercising the identical, already-complete `POST /api/v1/activate` contract delivered in Increment A (§1.2). |

Because `POST /api/v1/activate` is a Backend endpoint, it is fully built, testable, and acceptance-verifiable at the end of Increment A using any HTTP client. Increment B does not add new Backend behavior; it adds the Agent (or simulator) as a caller of behavior that already exists.

### 1.2 Simulator Compatibility

A simulated Jetson client is permitted, and expected, to execute Increment B's activation and reactivation behavior using the exact same production API contract defined in §10.4 — the same request fields, the same validation rules (§13), the same response envelope, and the same status codes. No simulator-only endpoint, parameter, header, or authentication bypass is introduced by this feature or may be introduced by an implementation of it. A simulated Agent and the real FastAPI Agent are indistinguishable to the Backend: both are simply callers presenting an Activation Key, and later a Device identity and shared secret, over the same endpoints.

### 1.3 Device Identity Model

This feature distinguishes two identifiers on the Device record, because a database row must exist before a device can be activated (to hold the reserved Activation Key), but the value a Jetson Agent uses to identify itself must never be exposed or assigned before activation actually succeeds:

| Identifier | Nature | Assigned | Visibility | Used For |
|------------|--------|----------|------------|----------|
| `DeviceRecordId` | Internal SQL Server primary key | At branch creation, together with the Device row itself | Never returned in any API response; internal-only | Foreign-key relationships (`Alert`, `HealthRecord`, `Configuration`, Activation Key record) |
| `DeviceId` | Persistent external device identity | On first successful activation only; nullable until then | Returned to the Dashboard only once the Device is activated; returned to the Agent on every successful activation/reactivation | `X-Device-Id` header on all Agent→Backend operational requests (specified in later Feature Specifications); correlation of historical alerts/health records |

Rules governing this model:

- Exactly one Device record exists per Branch; `Device.BranchId` is a unique foreign key (BR-002, CON-007).
- `DeviceId` is `NULL` from branch creation until the first successful activation.
- First activation assigns `DeviceId` a value for the first and only time; it is never reassigned afterward.
- Reactivation (§5.8) reuses the same `DeviceRecordId` row and the same, already-assigned `DeviceId` value; it never generates a new `DeviceId`.
- The Dashboard never displays `DeviceId` for an unactivated Device; once activated, it is shown as the device's persistent identity.
- Historical alerts and health records (created by later features) are associated with the persistent `DeviceId`, not with `DeviceRecordId`; this is what allows those records to remain correlated across a reactivation that replaces the shared secret but not the identity.

### 1.4 Activation Key Format and Secure Lookup

The Activation Key is a single string with two parts, separated by a delimiter:

```
<keyId>.<secret>
```

- `keyId` — a non-secret lookup identifier. It exists purely so the Backend can locate the correct Activation Key record by an indexed lookup, without scanning or comparing against every stored secret hash.
- `secret` — the actual credential material. The Backend never stores this in plaintext or in any recoverable form; only a salted adaptive hash of it is stored.

The Backend stores, per Activation Key record:

- `ActivationKeyId` — the non-secret `keyId` value, indexed for lookup.
- A salted adaptive hash of the `secret` portion only.
- The Activation Key's status (Unconsumed / Consumed / Invalidated).
- The associated `DeviceRecordId`.

The Backend **never** stores the complete plaintext key, and never stores the plaintext `secret` in any recoverable form (ARCH-001 §15.5).

## 2. Actors

| Actor | Description |
|-------|--------------|
| Admin User | The single authenticated account that creates branches, provides camera configuration, and manages (regenerates/resets) Activation Keys (BR-001, FS-01). |
| Installer | Configures the issued Activation Key on the physical Jetson device out-of-band (ASM-006). Not a system actor with API access; interacts with the Jetson device directly. |
| Jetson Agent (real or simulated) | Authenticates to the Backend using the Activation Key on first startup, and subsequently using its persistent `DeviceId` and shared secret. §1.2 establishes that a simulated Agent is a valid instance of this actor, indistinguishable to the Backend from the real FastAPI Agent. |
| ASP.NET Core Backend | Issues Activation Keys, validates activation requests, assigns/retains `DeviceId`, issues and rotates shared secrets. |

## 3. Preconditions

- The Admin is authenticated with a valid, non-revoked session (FS-01).
- No branch can have a Jetson device activated against it until the branch itself exists (BR-002).
- The calling Agent (real or simulated) has network reachability to the Backend at the moment of activation (ASM-004, ASM-008).

## 4. Functional Behavior

| Behavior | Requirement Basis | Increment |
|----------|---------------------|-----------|
| The Admin can create a branch, providing branch name, address, contact details, and one or more camera RTSP configurations. | FR-BRN-001 | A |
| The Backend generates a unique, two-part Activation Key upon branch creation. | FR-BRN-002 | A |
| A Jetson Agent can authenticate using a valid, unconsumed Activation Key and, on success, receives its `DeviceId`, shared secret, and full branch/camera/operational configuration. | FR-BRN-003 | A (Backend), B (Agent-side consumption) |
| An Activation Key is marked consumed after its first successful use; reuse of a consumed key is rejected. | FR-BRN-004, BR-003 | A |
| The Admin can regenerate/reset a branch's Activation Key, invalidating the previous key record and issuing a new `keyId`/`secret` pair. | FR-BRN-005 | A |
| A Jetson Agent cannot create or self-register a new branch. | FR-BRN-006, BR-002 | A |
| Once activated, a device retains a persistent `DeviceId` so the Backend can correlate all subsequent communications to it across restarts, reconnections, and reactivations. | FR-BRN-007 | A (Backend identity model), B (Agent-side persistence) |
| A branch must exist before any device can activate against it. | BR-002 | A |
| An Activation Key may only be consumed once; a new key must be issued (via regeneration) to reactivate or replace a device. | BR-003 | A |
| Physical installation and Activation Key configuration on the device are performed out-of-band by an installer, not automated by the system. | ASM-006 | B |
| The Backend can resolve a working network address for a registered device to communicate with it directly. | ASM-008 | A |
| Initial device activation uses a one-time Activation Key; after successful activation, the Backend issues a persistent `DeviceId` and shared secret that authenticate subsequent Agent-to-Backend operational requests. | NFR-SEC-002 | A (Backend), B (Agent-side use) |

## 5. Detailed Workflows

### Increment A Workflows

#### 5.1 Branch Creation

1. Admin submits branch details (name, address, contact details, one or more camera RTSP configurations) via the Dashboard.
2. Dashboard calls `POST /api/v1/branches` with a valid Admin session (FS-01).
3. `BranchService` creates the `Branch` record and one `Camera` record per submitted camera configuration.
4. `BranchService`/`DeviceService` creates a single associated `Device` record for the branch (`DeviceRecordId` assigned as the internal primary key; `DeviceId` left `NULL`; `ActivationStatus = Unactivated`).
5. `DeviceService` generates a new Activation Key: a random `keyId`, a random `secret`, and a salted hash of the `secret`. It stores `ActivationKeyId = keyId`, the secret hash, `Status = Unconsumed`, and the associated `DeviceRecordId`.
6. Backend returns the created branch/camera details and the complete plaintext Activation Key (`keyId.secret`) in the response. `DeviceId` is not present in this response, because the Device is not yet activated.
7. Dashboard displays the plaintext Activation Key to the Admin. This is the only point at which the plaintext key is ever shown as part of branch creation.

#### 5.2 Initial Activation Key Generation

Activation Key generation is not a standalone workflow independent of branch creation: per FR-BRN-002, the key is generated as an integral part of branch creation (§5.1, steps 4–7). No branch exists without an associated, unactivated Device record and Activation Key record from the moment of its creation.

#### 5.3 Key Regeneration

1. The Admin requests regeneration/reset of a branch's Activation Key via the Dashboard.
2. Dashboard calls the Backend's key-regeneration API with a valid Admin session.
3. The Backend sets the branch's current Activation Key record's status to Invalidated, regardless of its prior consumption state.
4. The Backend generates a new `keyId`, a new `secret`, and a salted hash of the new secret, storing a new Activation Key record (`Status = Unconsumed`) associated with the same `DeviceRecordId`.
5. The Backend returns the new complete plaintext Activation Key (`keyId.secret`).
6. Dashboard displays the new plaintext key to the Admin; the previous plaintext key is no longer valid for activation regardless of whether it was ever used.

#### 5.4 Branch/Device/Camera List and Detail Viewing

1. Admin navigates to the Branch Management module.
2. Dashboard calls the Backend to list branches (with associated device activation status) and, on selection, branch/camera/device detail.
3. Backend returns branch, camera, and device data. For an unactivated Device, only `ActivationStatus = Unactivated` is returned (no `DeviceId`). For an activated Device, `ActivationStatus = Activated` and the persistent `DeviceId` are returned, along with last-known health/contact summary as available from other features.
4. Dashboard renders the list and detail views; the plaintext Activation Key and the internal `DeviceRecordId` are never included in these read responses (only the single-disclosure responses of §5.1/§5.3 ever contain the plaintext key, and `DeviceRecordId` is never returned to any client, per §1.3).

### Increment B Workflows

#### 5.5 Successful First Activation

1. The installer configures the Jetson Agent (real or simulated, §1.2) with the complete plaintext Activation Key (ASM-006).
2. On startup, the Agent calls `POST /api/v1/activate` with the Activation Key.
3. The Backend parses the presented value into `keyId` and `secret`. A value that does not parse into exactly two non-empty parts is rejected as malformed before any lookup occurs.
4. The Backend looks up the Activation Key record by `ActivationKeyId = keyId`. No match → rejected (§5.6).
5. The Backend verifies the presented `secret` against the record's stored salted hash. No match → rejected (§5.6).
6. The Backend confirms the record's status is Unconsumed and not Invalidated (§5.7).
7. In a single atomic transaction, the Backend: marks the Activation Key record Consumed; assigns the associated Device record's `DeviceId` (first activation only); marks `ActivationStatus = Activated`; generates a new shared secret and stores it (protected, §12) as `ProtectedSharedSecret`.
8. The Backend returns the assigned `DeviceId`, the shared secret, and the branch/camera/operational configuration (confidence threshold, heartbeat interval, retention period, camera settings) to the Agent.
9. The Agent persists the `DeviceId`, the shared secret (protected per §12), and the received configuration in its local `DeviceIdentity` and `ConfigCache` stores.
10. Activation is complete; the Agent is now able to authenticate subsequent operational requests using `X-Device-Id: <DeviceId>` and `X-Device-Secret`.

#### 5.6 Invalid Key (Malformed, Unknown `keyId`, or Incorrect Secret)

1. An Agent calls `POST /api/v1/activate` with a value that is malformed (does not split into a `keyId` and a `secret`), or whose `keyId` matches no stored Activation Key record, or whose `secret` fails hash verification against a `keyId` that does exist.
2. The Backend rejects the request in each of these three cases with the same externally observable outcome (401), to avoid revealing which specific check failed.
3. No `DeviceId`, shared secret, or configuration is returned; no Device record is created or modified; no Activation Key record's status changes.

#### 5.7 Consumed or Invalidated Key Reuse

1. An Agent calls `POST /api/v1/activate` with a `keyId`/`secret` pair that resolves to a stored Activation Key record whose status is Consumed (already used in a prior activation, §5.5) or Invalidated (superseded by a regeneration, §5.3).
2. The Backend rejects the request.
3. The previously assigned `DeviceId`, shared secret, and Device record (if any) are unaffected by the rejected attempt.

#### 5.8 Reactivation

1. A branch's device needs to be reactivated (e.g., replacement Jetson unit, factory reset, credential recovery) after having previously completed a successful first activation (§5.5).
2. The Admin regenerates the branch's Activation Key (§5.3), which invalidates the old Activation Key record and creates a new one associated with the same `DeviceRecordId`.
3. The Agent (new or reset) calls `POST /api/v1/activate` with the new Activation Key.
4. The Backend validates the new key as in §5.5, steps 3–6.
5. Because the Device record already has an assigned `DeviceId` (from the original activation), the Backend **retains** that existing `DeviceId` rather than assigning a new one.
6. The Backend issues a **new** shared secret, atomically replacing the previous one; the previous shared secret is invalidated.
7. The Backend returns the retained `DeviceId`, the new shared secret, and current configuration to the Agent.
8. The Agent atomically replaces its locally stored shared secret with the new one; its locally stored `DeviceId` is unchanged (it matches what the Backend returns).
9. Historical alerts and health records remain correlated to the retained `DeviceId`; reactivation does not create a new logical device identity.

#### 5.9 Agent Startup After Previous Activation

1. On a normal restart (not a reactivation), the Agent loads its persistent `DeviceId`, protected shared secret, and cached configuration from local storage (`DeviceIdentity`, `ConfigCache`).
2. The Agent does not call `POST /api/v1/activate` again; that endpoint is used only for a device's first activation or an explicit reactivation following key regeneration.
3. The Agent resumes authenticated operational communication with the Backend using its existing `DeviceId` and shared secret. Starting/supervising DeepStream from cached configuration is specified in a later Feature Specification; FS-02 only establishes that the configuration required for that startup is already locally available.

## 6. Backend Responsibilities

**Increment A:**

- Create a `Branch` record and associated `Camera` record(s) from Admin-submitted branch-creation data (FR-BRN-001).
- Create a single associated `Device` record for the branch at creation time, with `DeviceRecordId` assigned, `DeviceId = NULL`, and `ActivationStatus = Unactivated`.
- Generate a unique, two-part (`keyId.secret`) Activation Key as an integral part of branch creation, storing only `ActivationKeyId` (the `keyId`) and a salted hash of the `secret` — never the complete plaintext key or plaintext secret (FR-BRN-002, ARCH-001 §15.5, §1.4).
- Return the complete plaintext Activation Key to the Dashboard only at the moment of generation (branch creation or regeneration); never return or re-derive the plaintext key afterward.
- Support Admin-initiated Activation Key regeneration for a branch: invalidate the previous Activation Key record regardless of its consumption state, and generate a new `keyId`/`secret` pair (FR-BRN-005).
- Provide branch/camera/device list and detail read endpoints, including device activation status and, only once activated, `DeviceId` — never `DeviceRecordId`, and never the plaintext Activation Key.
- Reject any request that would create or self-register a branch or device from a non-Admin-authenticated caller; branch creation is reachable only via the Admin-authenticated Dashboard API (FR-BRN-006, BR-002).
- Implement `POST /api/v1/activate` in full (parsing, lookup by `keyId`, secret verification, atomic consumption, `DeviceId` assignment/retention, shared-secret issuance, configuration return) even though it has no Angular surface — it is Backend-only work delivered within Increment A (§1.1).

**Increment B (Backend-side, already built in Increment A; re-stated here because it is exercised for the first time by a real or simulated Agent):**

- Parse the presented Activation Key into `keyId` and `secret`; reject malformed input before any lookup (FR-BRN-003, FR-BRN-004, §1.4).
- Look up the Activation Key record by `ActivationKeyId = keyId` — an indexed lookup, never a scan/comparison across all stored secret hashes.
- Verify the presented `secret` against the record's stored salted hash using the hashing service's verify function.
- Confirm the resolved record's status is Unconsumed and not Invalidated before proceeding (FR-BRN-004, BR-003).
- Mark the Activation Key record Consumed as part of the same atomic transaction that assigns/retains `DeviceId` and issues the shared secret, so that a key cannot be used twice even under concurrent or retried requests (FR-BRN-004, BR-003).
- Reject any activation attempt using an already-consumed or invalidated Activation Key, or an unknown `keyId`, or a `keyId` with an incorrect `secret`, without side effects on any existing Device record (FR-BRN-004, BR-003).
- On a device's first successful activation, assign the branch's existing (previously unactivated) Device record its persistent `DeviceId` and mark it activated (FR-BRN-007, §1.3).
- On reactivation of a branch that already has an activated Device record, retain the existing `DeviceId` rather than assigning a new one (ARCH-001 §16.4, ADR-015, §1.3).
- Issue a new shared secret on every successful activation (first activation and every reactivation), invalidating any previously issued shared secret for that Device (NFR-SEC-002, ADR-015).
- Resolve and retain the device's current network address as observable at each operational contact, to support later command delivery (ASM-008; realized in later feature specifications that use this address).
- Never store the complete plaintext Activation Key or plaintext secret in recoverable form, and never write the plaintext Activation Key, its secret portion, or the device shared secret to application logs (ARCH-001 §15.5, §15.6).

## 7. Angular Dashboard Responsibilities

**Increment A only — no Angular work is deferred to Increment B.**

- Present a branch-creation form collecting branch name, address, contact details, and one or more camera RTSP configurations, and submit it to the Backend under a valid Admin session (FS-01).
- Display the complete plaintext Activation Key returned at branch creation, clearly indicating it is shown only this once and must be recorded/transferred to the installer out-of-band.
- Provide an Admin-facing action to regenerate/reset a branch's Activation Key, and display the newly generated plaintext key using the same single-disclosure treatment as initial generation.
- Present branch, camera, and device list/detail state — including device activation status (Unactivated/Activated) and, only once activated, the persistent `DeviceId` — as returned by the Backend, without independently deriving or caching the plaintext Activation Key beyond the single display at generation/regeneration time.
- Never display or expect a `DeviceRecordId` value; it is an internal identifier not returned by any endpoint.
- Contain no activation business logic beyond branch-creation submission, key display, and status presentation (NFR-MNT-001).

## 8. Jetson Agent Responsibilities

**Increment B — implemented after Increment A, by the real Agent and, before that, by a simulated Agent under the identical contract (§1.2).**

- On first startup with a configured Activation Key and no existing local Device Identity, call `POST /api/v1/activate` with the complete plaintext key (`keyId.secret`).
- On successful activation, persist the returned `DeviceId`, shared secret, and configuration into local storage (`DeviceIdentity`, `ConfigCache`), protected per §12.
- On subsequent restarts where a local Device Identity already exists, load it from local storage rather than re-calling `POST /api/v1/activate`.
- On a reactivation flow (a new Activation Key configured after a prior successful activation), call `POST /api/v1/activate` again with the new key, and atomically replace the locally stored shared secret with the one returned, while the locally stored `DeviceId` is expected to remain the same value as returned by the Backend.
- Never attempt to create or register a branch; the Agent's only activation-related capability is presenting an Activation Key to `POST /api/v1/activate`.
- Never log the Activation Key, its `secret` portion, or the shared secret in plaintext.
- A simulated Agent fulfills every bullet above identically to the real Agent, using the same endpoint, fields, and headers; it may persist `DeviceIdentity`/`ConfigCache` equivalents in whatever lightweight form suits the simulator (e.g., a local file or in-memory store), provided the Backend-facing behavior is indistinguishable from the real Agent.

## 9. Data Requirements

| Entity/Field | Purpose | Increment Introduced |
|--------------|---------|--------------------------|
| `Branch.BranchId`, `Name`, `Address`, `ContactDetails` | Core branch identity and contact information (ARCH-001 §13.1). | A |
| `Camera.CameraId`, `BranchId`, `Name`, `RtspUrl`, `Enabled` | One or more cameras associated with a branch. | A |
| `Device.DeviceRecordId` (internal PK) | Internal SQL Server primary key; created with the Device row at branch creation; never returned by any API; used only for internal foreign-key relationships (§1.3). | A |
| `Device.DeviceId` (persistent, nullable) | `NULL` until first activation; assigned once, on first successful activation; retained unchanged through every subsequent reactivation; the only device identifier ever exposed externally or used in `X-Device-Id` (FR-BRN-007, ADR-015, §1.3). | A (column exists, null), B (first assigned) |
| `Device.BranchId` (unique FK) | Associates the Device with its branch; enforces exactly one Device per Branch (BR-002, CON-007). | A |
| `Device.ActivationStatus` | Unactivated / Activated; read by Increment A's list/detail views, written by Increment B's activation flow. | A (read), B (write) |
| `Device.ProtectedSharedSecret` | Backend-side recoverable-but-protected form of the current shared secret, used to authenticate Backend→Agent commands (ARCH-001 §13.3). Nullable until activation. | B |
| `Device.LastKnownAddress` | Most recently observed network address, supporting ASM-008. Nullable until first operational contact. | B |
| `ActivationKey.ActivationKeyId` (the `keyId`) | Non-secret lookup identifier for an Activation Key record; indexed (§1.4). | A |
| `ActivationKey.SecretHash` | Salted adaptive hash of the `secret` portion only; never the plaintext secret. | A |
| `ActivationKey.Status` | Unconsumed / Consumed / Invalidated. | A |
| `ActivationKey.DeviceRecordId` (FK) | Associates the Activation Key record with its Device record (internal PK, not the external `DeviceId`). | A |
| `DeviceIdentity` (Jetson SQLite, real or simulated equivalent) | The Agent's persistent local record of its own `DeviceId` and protected shared secret (ARCH-001 §13.2). | B |
| `ConfigCache` (Jetson SQLite, real or simulated equivalent) | The Agent's persistent local record of the most recently synchronized configuration, used at startup independent of Backend availability (ARCH-001 §13.2, §16.3). | B |

No binary media is introduced or referenced by this feature; only identity, credential, and configuration metadata are in scope (BR-006 is not engaged by FS-02).

## 10. API Contracts (Feature-Specification Level)

All endpoints below are real, production endpoints. None is simulator-specific; a simulated Agent (§1.2) calls exactly these, with exactly these contracts.

### 10.1 Branch Creation — Increment A

| Aspect | Detail |
|--------|--------|
| Endpoint | `POST /api/v1/branches` |
| Auth required | Valid Admin JWT + active session (FS-01) |
| Request fields | Branch name, address, contact details, one or more camera configurations (name, RTSP URL) |
| Success response | Standard envelope; `data` contains created branch/camera details, the reserved (unactivated) Device summary (`activationStatus: "Unactivated"`, no `deviceId`), and the complete plaintext Activation Key (`keyId.secret`) |
| Success status code | 201 |
| Failure (invalid/incomplete submission) | Standard error envelope |
| Failure status code | 400 |
| Failure (no/invalid session) | Standard error envelope |
| Failure status code | 401 |

### 10.2 Activation Key Regeneration — Increment A

| Aspect | Detail |
|--------|--------|
| Endpoint | Backend endpoint scoped to the target branch/device for Activation Key regeneration (exact route deferred — §19) |
| Auth required | Valid Admin JWT + active session (FS-01) |
| Request fields | None beyond the target branch/device identifier |
| Success response | Standard envelope; `data` contains the new complete plaintext Activation Key (`keyId.secret`) |
| Success status code | 200 |
| Effect | Previous Activation Key record invalidated regardless of consumption state; new Activation Key record (new `keyId`, new secret hash) generated, unconsumed |
| Failure (branch/device not found) | Standard error envelope |
| Failure status code | 404 |
| Failure (no/invalid session) | Standard error envelope |
| Failure status code | 401 |

### 10.3 Branch/Device/Camera List and Detail — Increment A

| Aspect | Detail |
|--------|--------|
| Endpoint | `GET /api/v1/branches`, `GET /api/v1/branches/{id}`, `GET /api/v1/devices/{id}` (per ARCH-001 §14.1) |
| Auth required | Valid Admin JWT + active session (FS-01) |
| Success response | Standard envelope; `data` contains branch/camera/device details including device activation status and, only if activated, `deviceId`; never includes `DeviceRecordId` or the plaintext Activation Key |
| Success status code | 200 |
| Failure (not found) | Standard error envelope |
| Failure status code | 404 |

### 10.4 `POST /api/v1/activate` — Built in Increment A, Consumed in Increment B

| Aspect | Detail |
|--------|--------|
| Endpoint | `POST /api/v1/activate` |
| Auth required | None (exempt from Admin JWT authentication per FR-AUT-002/FS-01; authenticated instead by the Activation Key itself) |
| Request fields | Complete plaintext Activation Key (`keyId.secret`) |
| Success response | Standard envelope; `data` contains `deviceId`, shared secret, and branch/camera/operational configuration |
| Success status code | 200 |
| Failure (malformed key / unknown `keyId` / incorrect secret) | Standard error envelope; no `deviceId`/secret/config returned; identical externally observable outcome for all three (§5.6) |
| Failure status code | 401 |
| Failure (key already consumed or invalidated) | Standard error envelope; no `deviceId`/secret/config returned; no change to any existing Device record |
| Failure status code | 401 |
| Caller | Real FastAPI Agent or a simulated Agent client (§1.2) — identical contract for both |

All responses use the uniform response envelope defined in ARCH-001 §14.3 / ADR-009. Exemption from Admin JWT authentication applies only to that specific check; the endpoint still performs full request validation (§12), Activation Key validation, standard error handling (§13), and uses the standard response envelope like any other endpoint.

## 11. Security Rules

- The Activation Key is a one-time credential: consumed atomically on first successful use and never valid for a subsequent activation attempt until explicitly regenerated by the Admin (FR-BRN-004, BR-003).
- The Activation Key's `secret` portion is stored by the Backend only as a salted adaptive hash; the `keyId` portion is stored and indexed as a non-secret lookup value; the complete plaintext key and the plaintext secret exist only transiently at generation/regeneration time and in the corresponding single API response (ARCH-001 §15.5, §1.4).
- Activation Key lookup is always by indexed `keyId`; the Backend never iterates over or compares against every stored secret hash to resolve an activation request.
- The plaintext Activation Key, its `secret` portion, and the device shared secret are never written to application logs, on either the Backend or the Agent (ARCH-001 §15.6).
- The device shared secret is stored by the Backend in a recoverable but protected form (required for Backend→Agent command authentication in later features) and by the Agent under restrictive local file permissions; it is never written to application logs (ARCH-001 §13.3, §15.6).
- After successful activation, the persistent `DeviceId` and shared secret — not the Activation Key — authenticate all subsequent Agent-to-Backend operational requests (NFR-SEC-002).
- Reactivation issues a new shared secret and invalidates the previous Activation Key record; it does not issue a new `DeviceId` for the same logical device (ARCH-001 §16.4, ADR-015, §1.3).
- The internal `DeviceRecordId` is never exposed in any API response, log, or Dashboard view; it exists solely as an internal relational key (§1.3).
- No certificates, mTLS, OAuth/OIDC, HMAC signing, or key-rotation scheduling beyond the approved reactivation secret replacement is introduced by this feature (ARCH-001 §15.6, §28.2).
- Branch creation and Activation Key regeneration are reachable only through the Admin-authenticated Dashboard API; no endpoint allows a Jetson Agent to create a branch or device record (FR-BRN-006, BR-002).
- No simulator-only endpoint, request parameter, header, or authentication bypass exists or may be introduced; a simulated Agent authenticates and is validated by exactly the same rules as the real Agent (§1.2).

## 12. Validation Rules

- A branch-creation request must include branch name, address, contact details, and at least one camera configuration with a valid RTSP URL; an incomplete request is rejected before any Branch/Camera/Device/Activation Key record is created.
- An activation request must include a non-empty Activation Key value that parses into exactly a `keyId` and a `secret` component; a request missing the key, or one that does not parse into both components, is rejected before any lookup is attempted.
- A `keyId` presented on `POST /api/v1/activate` must resolve to exactly one stored Activation Key record; no match results in rejection.
- Where a `keyId` resolves to a record, the presented `secret` must verify against that record's stored salted hash; a non-matching secret results in rejection, with the same externally observable outcome as an unknown `keyId` (§5.6).
- A resolved, secret-verified Activation Key record must be in the Unconsumed, non-Invalidated state to proceed; any other state (Consumed, Invalidated by regeneration) results in rejection.
- Marking an Activation Key record Consumed, assigning/retaining `DeviceId`, and issuing the new shared secret must occur as a single atomic operation, so that a race between two concurrent activation attempts using the same key cannot result in two callers being issued credentials from one key. Under such a race, exactly one attempt succeeds and the other observes the key as already Consumed.

## 13. Error Cases

| Case | Handling | Increment |
|------|----------|-----------|
| Branch-creation request missing required fields | 400; no Branch/Camera/Device/Activation Key created | A |
| Branch-creation request without a valid Admin session | 401; request rejected before business logic | A |
| Key-regeneration request for a non-existent branch/device | 404 | A |
| Key-regeneration request without a valid Admin session | 401; request rejected before business logic | A |
| Jetson Agent (real or simulated) attempts to call a branch/device-creation endpoint | 401 (no valid Admin session); no branch/device is created by any non-Admin caller | A |
| Activation request with a malformed key (no parseable `keyId`/`secret`) | 401; rejected before any lookup | B |
| Activation request with an unknown `keyId` | 401; no `deviceId`/secret/config returned | B |
| Activation request with a correct `keyId` but incorrect `secret` | 401; identical outcome to an unknown `keyId`; no side effects | B |
| Activation request with an already-consumed Activation Key | 401; no change to the existing Device record | B |
| Activation request with a key invalidated by a subsequent regeneration | 401; no `deviceId`/secret/config returned | B |
| Two concurrent activation requests presenting the same valid, unconsumed key | Exactly one succeeds (per §12's atomicity rule); the other receives the same rejection as a consumed-key reuse | B |

## 14. Acceptance Criteria

| # | Acceptance Criterion | Traces To | Increment |
|---|------------------------|-----------|-----------|
| AC-1 | A branch created via the Dashboard with name, address, contact details, and camera configuration(s) produces a persisted Branch record with associated Camera record(s) and a reserved, unactivated Device record (`DeviceId = NULL`). | FR-BRN-001 | A |
| AC-2 | Branch creation produces a unique, two-part Activation Key, shown in complete plaintext to the Admin exactly once, at generation time. | FR-BRN-002 | A |
| AC-3 | A Jetson Agent (real or simulated) presenting a valid, unconsumed Activation Key to `POST /api/v1/activate` receives a `DeviceId`, shared secret, and its branch/camera/operational configuration, and activates successfully. | FR-BRN-003 | A (endpoint), B (first exercised) |
| AC-4 | An Activation Key is marked consumed after its first successful use, and a subsequent activation attempt using the same key is rejected. | FR-BRN-004 | A (endpoint), B (first exercised) |
| AC-5 | Regenerating a branch's Activation Key invalidates the previous Activation Key record such that a subsequent activation attempt using the old key fails, regardless of whether the old key had already been consumed, and a new `keyId`/`secret` pair is issued. | FR-BRN-005 | A |
| AC-6 | No API path exists by which a Jetson Agent (real or simulated) can create or self-register a new branch. | FR-BRN-006 | A |
| AC-7 | A device's persistent `DeviceId` is assigned exactly once, on first activation, and is retained unchanged across restarts, reconnections, and a reactivation using a newly regenerated key, such that the Backend correlates all communications to the correct device. | FR-BRN-007 | A (model), B (first exercised) |
| AC-8 | No device can be activated against a branch that does not already exist. | BR-002 | A |
| AC-9 | An Activation Key can be consumed at most once; reactivating or replacing a device requires a newly regenerated key. | BR-003 | A (endpoint), B (first exercised) |
| AC-10 | Physical installation and Activation Key configuration on the Jetson device are performed out-of-band by an installer; the system provides no automated remote provisioning path. | ASM-006 | B |
| AC-11 | The Backend can resolve a working network address for a registered device following activation and subsequent operational contact. | ASM-008 | B |
| AC-12 | Initial activation uses a one-time Activation Key; upon success, the Backend issues a persistent `DeviceId` and shared secret that authenticate subsequent Agent-to-Backend operational requests. | NFR-SEC-002 | A (endpoint), B (first exercised) |
| AC-13 | A simulated Jetson client can execute the entire activation and reactivation flow (AC-3, AC-4, AC-7, AC-9, AC-12) using the exact same production endpoint and contract as the real Agent, with no simulator-specific accommodation on the Backend. | Delivery-strategy requirement (§1.2) | B |
| AC-14 | Activation Key lookup is performed by indexed `keyId`, never by scanning/comparing every stored secret hash; the Backend never stores the complete plaintext key or plaintext secret in recoverable form. | NFR-SEC-002, ARCH-001 §15.5 (§1.4) | A |
| AC-15 | A malformed Activation Key, an unknown `keyId`, and a correct `keyId` with an incorrect `secret` all produce the same externally observable rejection, with no Device record side effects. | FR-BRN-003, FR-BRN-004 (§5.6) | B |
| AC-16 | Under concurrent activation attempts presenting the same valid, unconsumed Activation Key, exactly one attempt succeeds and no two callers are ever issued credentials from the same key. | FR-BRN-004, BR-003 (§12) | B |

## 15. Test Scenarios

### Increment A — Backend and Angular

- T-01: Admin submits a complete branch-creation request → Branch, Camera record(s), and a reserved Device record (`DeviceId = NULL`, `ActivationStatus = Unactivated`) are created → unique two-part Activation Key generated and shown once in the response.
- T-02: Admin submits a branch-creation request missing a required field → rejected (400); no Branch/Camera/Device/Activation Key created.
- T-03: Admin regenerates the Activation Key for a branch whose original key was never consumed → previous Activation Key record invalidated → new `keyId`/`secret` pair generated and shown once.
- T-04: Admin (or any caller without a valid session) attempts branch creation or key regeneration without authentication → rejected (401).
- T-05: Admin views the branch list/detail — the newly created branch shows its Device as "Unactivated" with no `deviceId` present; the plaintext Activation Key is not present in this view.
- T-06: An unauthenticated request, or a request bearing only a placeholder Device shared secret, attempts to reach the branch-creation endpoint → rejected; no branch is created (verifies AC-6 at the Increment A stage using a hand-crafted request, since no Agent yet exists).

### Increment B — Agent Activation (Real or Simulated)

- T-07: Agent (real or simulated) calls `POST /api/v1/activate` with the valid, unconsumed key from T-01 → activation succeeds → `DeviceId`, shared secret, and configuration returned → Activation Key record marked Consumed → Device shown as "Activated" with the assigned `DeviceId` in the Increment A views.
- T-08: Agent repeats the same activation call with the now-consumed key → rejected (401); no change to the Device record created in T-07.
- T-09: Agent calls `POST /api/v1/activate` with a malformed value (no `.` separator, or empty `keyId`/`secret`) → rejected (401); no lookup performed; no Device record created.
- T-10: Agent calls `POST /api/v1/activate` with an unknown `keyId` → rejected (401); no Device record created.
- T-11: Agent calls `POST /api/v1/activate` with a correct `keyId` (from T-01) but an incorrect `secret` → rejected (401), with the same observable outcome as T-10.
- T-12: Agent attempts activation using a key invalidated by a T-03-style regeneration → rejected (401).
- T-13: Agent activates using a freshly regenerated key (post-T-03) → succeeds, following the same behavior as T-07.
- T-14: A branch already has an activated Device (from T-07/T-13). Admin regenerates the Activation Key. Agent (new or reset) activates using the new key → the returned `DeviceId` matches the branch's existing `DeviceId` (retained, not reassigned) → a new shared secret is issued, replacing the previous one.
- T-15: Historical alert/health records associated with the retained `DeviceId` (created in a prior feature's test data, referenced here only for correlation) remain queryable and correlated to that same `DeviceId` after reactivation.
- T-16: Agent restarts with an existing local `DeviceIdentity` and `ConfigCache` → Agent does not call `POST /api/v1/activate` again → Agent resumes operation using its existing `DeviceId` and shared secret.
- T-17: Two concurrent activation requests are issued against the same valid, unconsumed key (from a fresh branch, distinct from T-01's) → exactly one succeeds with a `DeviceId` assigned; the other receives the same rejection as a consumed-key reuse; no two `DeviceId`s are issued from the one key.
- T-18: The same test sequence (T-07–T-17) is executed once using a simulated Jetson client and, later, once using the real FastAPI Agent, against the same Backend build, with identical observable Backend behavior in both runs (verifies AC-13).

## 16. Increment A / Increment B Implementation Boundary — Summary

| Aspect | Increment A | Increment B |
|--------|--------------|--------------|
| Branch/Camera/Device data model (including `DeviceRecordId`/`DeviceId` split) | Fully implemented | No change |
| Branch creation, list, detail | Fully implemented | No change |
| Activation Key generation/regeneration (`keyId.secret` format, hashed storage) | Fully implemented | No change |
| `POST /api/v1/activate` (Backend logic, incl. malformed/unknown-key/wrong-secret/consumed/invalidated/concurrent handling) | Fully implemented and independently testable via any HTTP client | No change — only gains a real/simulated caller |
| Device activation-status display, incl. `DeviceId` reveal on activation | Fully implemented | Reflects state written by Increment B activations |
| Agent-side activation client | Not present | Implemented (real Agent), previously exercised by a simulator (§1.2) |
| `DeviceIdentity` / `ConfigCache` persistence | Not present | Implemented |
| Reactivation (Agent-side behavior) | Not present | Implemented |

No acceptance criterion, security rule, or validation rule differs between the two increments; only the presence of a real or simulated caller for the already-complete Backend endpoints changes.

## 17. Out of Scope

Consistent with SRS §11, CON-007, and ARCH-001 §1.5/§28.2, this feature explicitly excludes:

- Device self-registration or any Agent-initiated branch/device creation.
- Multiple Jetson devices per branch.
- Remote or zero-touch device provisioning; Activation Key configuration on the device remains an out-of-band, installer-performed action (ASM-006).
- Certificates, mTLS, OAuth/OIDC, HMAC signing, or any key-rotation scheduling beyond the single shared-secret replacement that occurs on reactivation.
- Exposing the plaintext Activation Key, its `secret` portion, or the internal `DeviceRecordId` at any point other than the initial generation/regeneration response (for the key) — `DeviceRecordId` is never exposed at all.
- Recoverable plaintext storage of the Activation Key or its secret portion on the Backend.
- Starting or supervising the DeepStream pipeline; activation returns configuration only, and Agent bootstrap/DeepStream supervision is specified in a later Feature Specification.
- The detailed behavior of ongoing Agent→Backend operational endpoints (heartbeat, alerts, sync, config retrieval) that use the `DeviceId`/shared secret issued here; those are specified in their own respective Feature Specifications.
- Any simulator-only endpoint, parameter, or authentication bypass (§1.2, §11).

## 18. Traceability Matrix

| Requirement/Decision | Realized In This Feature |
|------------------------|----------------------------|
| FR-BRN-001 | §5.1, §6, §10.1, AC-1 |
| FR-BRN-002 | §5.1, §5.2, §6, §10.1, §11, AC-2 |
| FR-BRN-003 | §5.5, §5.6, §6, §10.4, AC-3, AC-15 |
| FR-BRN-004 | §5.5, §5.7, §6, §11, §12, AC-4, AC-16 |
| FR-BRN-005 | §5.3, §6, §10.2, AC-5 |
| FR-BRN-006 | §5.1, §6, §8, §11, AC-6 |
| FR-BRN-007 | §1.3, §5.5, §5.8, §5.9, §9, §11, AC-7 |
| BR-002 | §1.3, §3, §6, §11, AC-8 |
| BR-003 | §5.5, §5.7, §5.3, §6, §11, AC-9, AC-16 |
| ASM-006 | §2, §5.5, §8, AC-10 |
| ASM-008 | §6, §9, AC-11 |
| NFR-SEC-002 | §5.5, §5.8, §6, §11, AC-12, AC-14 |
| ARCH-001 §16.4, ADR-015 | §1.3, §5.8, §6, §9, §11 (DeviceId retention, secret rotation) |
| ARCH-001 §13.2, ADR-004 | §5.5, §5.9, §9 (DeviceIdentity, ConfigCache) |
| ARCH-001 §13.3, §15.5 | §1.4, §6, §11 (credential/Activation Key storage) |
| ADR-002 (amended) | §5.5, §10.4, §11 (Activation Key → DeviceId/shared-secret exchange) |
| Simulator-compatibility delivery requirement | §1.2, §8, §11, AC-13, T-18 |

## 19. Open Implementation Details (Deferred to Implementation Plan)

The following are implementation parameters, not requirements, and are intentionally left open for the Implementation Plan phase:

- Exact route/path for the Activation Key regeneration endpoint (e.g., under `/api/v1/branches/{id}` vs. `/api/v1/devices/{id}`).
- Exact length/character set of the generated `keyId` and `secret`, and the specific salted adaptive hashing algorithm used for the `secret`.
- Exact delimiter character used between `keyId` and `secret` (specified conceptually as `.` in §1.4; confirmed at implementation time).
- Exact format/type of the persistent `DeviceId` (e.g., GUID vs. another externally-safe identifier scheme).
- Exact fields comprising "contact details" for a branch.
- Exact mechanism/timing by which the Backend updates `Device.LastKnownAddress` (e.g., derived from the activation request's originating address vs. a later operational contact).
- Exact concurrency-control mechanism (e.g., database transaction isolation level, unique constraint) used to guarantee atomic Activation Key consumption under concurrent activation attempts (AC-16).
- Exact form of the simulated Jetson client (e.g., a script, a small standalone console app) — only its adherence to the production API contract (§1.2, §10) is a requirement of this feature.

---

*FS-02 — Branch & Device Onboarding is FINAL — Approved and Frozen.*
