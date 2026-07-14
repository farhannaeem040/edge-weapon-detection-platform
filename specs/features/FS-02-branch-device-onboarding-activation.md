> **Superseded** by `specs/features/FS-02-branch-device-onboarding.md`, which restructures this same, unchanged requirement scope into Increment A (Backend/Angular) and Increment B (Agent activation) per the Backend/Angular-first delivery strategy. Retained here for history only; do not implement from this file.

# Feature Specification: Branch & Device Onboarding (Activation)

| Field | Value |
|-------|-------|
| Feature ID | FS-02 |
| Title | Branch & Device Onboarding (Activation) |
| Status | Draft |
| Related SRS Requirements | FR-BRN-001, FR-BRN-002, FR-BRN-003, FR-BRN-004, FR-BRN-005, FR-BRN-006, FR-BRN-007, BR-002, BR-003, ASM-006, ASM-008, NFR-SEC-002 |
| Related Architecture Sections | ARCH-001 §10.1 (BranchController/DeviceController, BranchService/DeviceService), §13.1 (Branch, Camera, Device entities), §13.2 (DeviceIdentity, ConfigCache), §13.3 (Credential/Secret Storage), §14.1 (Backend API), §15.1/§15.5 (Trust Boundaries, Activation Key Storage), §16 (Device Lifecycle) |
| Related ADRs | ADR-002 (amended) — Security Architecture; ADR-004 — Local Persistence; ADR-008 — Filesystem Layout; ADR-015 — Device Reactivation Policy |
| Owner | Farhan Naeem |
| Dependencies | FS-01 (Admin must be authenticated to create/manage branches and regenerate Activation Keys via the Dashboard) |

---

## 1. Purpose and Scope

This feature specifies the behavior required for a System Administrator to register a branch and its cameras, for the Backend to issue a one-time Activation Key for that branch, and for a Jetson Agent to exchange that key for a persistent Device ID and shared secret during activation. It also specifies Activation Key regeneration and the reactivation of a previously activated device.

This feature realizes FR-BRN-001–007, BR-002, BR-003, ASM-006, ASM-008, and NFR-SEC-002 as architected in ARCH-001 §16 (Device Lifecycle) and §15 (Security Architecture). It covers only the single-device-per-branch, Admin-initiated onboarding model established by the SRS and ARCH-001; it does not introduce device self-registration, multiple devices per branch, or remote zero-touch provisioning.

**Boundary with other features.** FS-02 covers branch/device onboarding and the activation/reactivation exchange only. Ongoing Agent→Backend operations that use the `X-Device-Id`/`X-Device-Secret` headers issued by this feature — heartbeat, alert/snapshot submission, offline-event sync, configuration retrieval — are specified in their own respective Feature Specifications (health monitoring, detection/alerting, offline resilience, configuration management) and are referenced here only to describe what the activation exchange produces. Starting the DeepStream pipeline is part of Agent bootstrap and is covered by a later Feature Specification (Jetson Agent Bootstrap & DeepStream Supervision); FS-02 specifies only that activation returns the configuration the Agent will use for that startup.

## 2. Actors

| Actor | Description |
|-------|--------------|
| Admin User | The single authenticated account that creates branches, provides camera configuration, and manages (regenerates/resets) Activation Keys (BR-001, FS-01). |
| Installer | Configures the issued Activation Key on the physical Jetson device out-of-band (ASM-006). Not a system actor with API access; interacts with the Jetson device directly. |
| Jetson Agent | Authenticates to the Backend using the Activation Key on first startup, and subsequently using its persistent Device ID and shared secret. |
| ASP.NET Core Backend | Issues Activation Keys, validates activation requests, assigns/retains persistent Device IDs, issues and rotates shared secrets. |

## 3. Preconditions

- The Admin is authenticated with a valid, non-revoked session (FS-01).
- No branch can have a Jetson device activated against it until the branch itself exists (BR-002).
- The Jetson device has network reachability to the Backend at the moment of activation (ASM-004, ASM-008).

## 4. Functional Behavior

| Behavior | Requirement Basis |
|----------|---------------------|
| The Admin can create a branch, providing branch name, address, contact details, and one or more camera RTSP configurations. | FR-BRN-001 |
| The Backend generates a unique Activation Key upon branch creation. | FR-BRN-002 |
| A Jetson Agent can authenticate using a valid, unconsumed Activation Key and, on success, receives its full branch/camera/operational configuration. | FR-BRN-003 |
| An Activation Key is marked consumed after its first successful use; reuse of a consumed key is rejected. | FR-BRN-004, BR-003 |
| The Admin can regenerate/reset a branch's Activation Key, invalidating the previous key. | FR-BRN-005 |
| A Jetson Agent cannot create or self-register a new branch. | FR-BRN-006, BR-002 |
| Once activated, a device retains a persistent identity so the Backend can correlate all subsequent communications to it across restarts and reconnections. | FR-BRN-007 |
| A branch must exist before any device can activate against it. | BR-002 |
| An Activation Key may only be consumed once; a new key must be issued (via regeneration) to reactivate or replace a device. | BR-003 |
| Physical installation and Activation Key configuration on the device are performed out-of-band by an installer, not automated by the system. | ASM-006 |
| The Backend can resolve a working network address for a registered device to communicate with it directly. | ASM-008 |
| Initial device activation uses a one-time Activation Key; after successful activation, the Backend issues a persistent Device ID and shared secret that authenticate subsequent Agent-to-Backend operational requests. | NFR-SEC-002 |

## 5. Detailed Workflows

### 5.1 Branch Creation

1. Admin submits branch details (name, address, contact details, one or more camera RTSP configurations) via the Dashboard.
2. Dashboard calls the branch-creation Backend API with a valid Admin session (FS-01).
3. `BranchService` creates the `Branch` record and one `Camera` record per submitted camera configuration.
4. `BranchService` (or `DeviceService`, per §7) generates a unique Activation Key for the new branch, stores it in hashed form, and marks its status unconsumed.
5. Backend returns the created branch/camera details and the plaintext Activation Key in the response.
6. Dashboard displays the plaintext Activation Key to the Admin. This is the only point at which the plaintext key is ever shown.

### 5.2 Initial Activation Key Generation

Activation Key generation is not a standalone workflow independent of branch creation in this feature: per FR-BRN-002, the key is generated as an integral part of branch creation (§5.1, steps 4–6). No branch exists without an associated Activation Key from the moment of its creation.

### 5.3 Successful First Activation

1. The installer configures the Jetson Agent with the plaintext Activation Key (ASM-006).
2. On startup, the Agent calls `POST /api/v1/activate` with the Activation Key.
3. The Backend validates: the key exists, hashes to a stored Activation Key record, is not already consumed, and is not invalidated (e.g., by a subsequent regeneration).
4. In the same transaction, the Backend marks the key consumed, assigns a new persistent Device ID, generates a new shared secret, and associates the Device with the branch.
5. The Backend returns the persistent Device ID, the shared secret, and the branch/camera/operational configuration (confidence threshold, heartbeat interval, retention period, camera settings) to the Agent.
6. The Agent persists the Device ID, the shared secret (protected per §12), and the received configuration in its local `DeviceIdentity` and `ConfigCache` stores.
7. Activation is complete; the Agent is now able to authenticate subsequent operational requests using `X-Device-Id` and `X-Device-Secret`.

### 5.4 Invalid Key

1. An Agent calls `POST /api/v1/activate` with an Activation Key that does not match any stored Activation Key record (e.g., mistyped, unknown).
2. The Backend finds no matching record and rejects the request.
3. No Device ID, shared secret, or configuration is returned; no Device record is created or modified.

### 5.5 Consumed Key Reuse

1. An Agent calls `POST /api/v1/activate` with an Activation Key that was already successfully consumed in a prior activation (§5.3).
2. The Backend finds the key's stored status as consumed and rejects the request.
3. The previously assigned Device ID, shared secret, and Device record are unaffected by the rejected attempt.

### 5.6 Key Regeneration

1. The Admin requests regeneration/reset of a branch's Activation Key via the Dashboard, for a branch whose current key is unconsumed (not yet used for a first activation) or where the Admin wishes to invalidate the outstanding key.
2. Dashboard calls the Backend's key-regeneration API with a valid Admin session.
3. The Backend invalidates the branch's previous Activation Key (it can never again be successfully consumed) and generates a new unique Activation Key in hashed form, unconsumed.
4. The Backend returns the new plaintext Activation Key.
5. Dashboard displays the new plaintext key to the Admin; the previous plaintext key is no longer valid for activation regardless of whether it was ever used.

### 5.7 Reactivation

1. A branch's device needs to be reactivated (e.g., replacement Jetson unit, factory reset, credential recovery) after having previously completed a successful first activation (§5.3).
2. The Admin regenerates the branch's Activation Key (§5.6).
3. The Agent (new or reset) calls `POST /api/v1/activate` with the new Activation Key.
4. The Backend validates the new key as in §5.3.
5. Because a Device record already exists for this branch (from the original activation), the Backend **retains** the existing persistent Device ID rather than assigning a new one.
6. The Backend issues a **new** shared secret, atomically replacing the previous one; the previous shared secret is invalidated.
7. The Backend returns the retained Device ID, the new shared secret, and current configuration to the Agent.
8. The Agent atomically replaces its locally stored shared secret with the new one.
9. Historical alerts and health records remain correlated to the retained Device ID; reactivation does not create a new logical device identity.

### 5.8 Agent Startup After Previous Activation

1. On a normal restart (not a reactivation), the Agent loads its persistent Device ID, protected shared secret, and cached configuration from local storage (`DeviceIdentity`, `ConfigCache`).
2. The Agent does not call `POST /api/v1/activate` again; that endpoint is used only for a device's first activation or an explicit reactivation following key regeneration.
3. The Agent resumes authenticated operational communication with the Backend using its existing Device ID and shared secret, and starts/supervises DeepStream using the cached configuration, independent of Backend availability at that moment (§16.3 of ARCH-001; DeepStream bootstrap detail itself belongs to a later Feature Specification).

## 6. Backend Responsibilities

- Create a `Branch` record and associated `Camera` record(s) from Admin-submitted branch-creation data (FR-BRN-001).
- Generate a unique Activation Key as an integral part of branch creation, storing it in non-recoverable hashed form and marking it unconsumed (FR-BRN-002, §15.5 of ARCH-001).
- Return the plaintext Activation Key to the Dashboard only at the moment of generation (branch creation or regeneration); never return or re-derive the plaintext key afterward.
- Validate, on `POST /api/v1/activate`, that the presented key hashes to a stored, unconsumed, non-invalidated Activation Key record before proceeding (FR-BRN-003, FR-BRN-004).
- Mark the Activation Key consumed as part of the same transaction that assigns/retains the Device ID and issues the shared secret, so that a key cannot be used twice even under concurrent or retried requests (FR-BRN-004, BR-003).
- Reject any activation attempt using an already-consumed or invalidated Activation Key, without side effects on any existing Device record (FR-BRN-004, BR-003).
- On a device's first successful activation for a branch, assign a new persistent Device ID (FR-BRN-007).
- On reactivation of a branch that already has an associated Device record, retain the existing persistent Device ID rather than assigning a new one (ARCH-001 §16.4, ADR-015).
- Issue a new shared secret on every successful activation (first activation and every reactivation), invalidating any previously issued shared secret for that Device (NFR-SEC-002, ADR-015).
- Reject any request that would create or self-register a branch or device from the Agent side; branch creation is reachable only via the Admin-authenticated Dashboard API (FR-BRN-006, BR-002).
- Support Admin-initiated Activation Key regeneration for a branch, invalidating the previous key regardless of its consumption state (FR-BRN-005).
- Resolve and retain the device's current network address as reported/observable at each operational contact, to support later command delivery (ASM-008; realized in later feature specifications that use this address).
- Never store the Activation Key in recoverable plaintext, and never write the plaintext Activation Key or the device shared secret to application logs (ARCH-001 §15.5, §15.6).

## 7. Dashboard Responsibilities

- Present a branch-creation form collecting branch name, address, contact details, and one or more camera RTSP configurations, and submit it to the Backend under a valid Admin session (FS-01).
- Display the plaintext Activation Key returned at branch creation, clearly indicating it is shown only this once and must be recorded/transferred to the installer out-of-band.
- Provide an Admin-facing action to regenerate/reset a branch's Activation Key, and display the newly generated plaintext key using the same single-disclosure treatment as initial generation.
- Present branch and device state (e.g., activation status) as returned by the Backend, without independently deriving or caching the plaintext Activation Key beyond the single display at generation/regeneration time.
- Contain no activation business logic beyond branch-creation submission and key display (NFR-MNT-001).

## 8. Jetson Agent Responsibilities

- On first startup with a configured Activation Key and no existing local Device Identity, call `POST /api/v1/activate` with the key.
- On successful activation, persist the returned Device ID, shared secret, and configuration into local storage (`DeviceIdentity`, `ConfigCache`), protected per §12.
- On subsequent restarts where a local Device Identity already exists, load it from local storage rather than re-calling `POST /api/v1/activate`.
- On a reactivation flow (a new Activation Key configured after a prior successful activation, e.g., following device replacement or reset), call `POST /api/v1/activate` again with the new key, and atomically replace the locally stored shared secret with the one returned, while the Device ID is expected to remain the same value as returned by the Backend.
- Never attempt to create or register a branch; the Agent's only activation-related capability is presenting an Activation Key to `POST /api/v1/activate`.
- Never log the Activation Key or the shared secret in plaintext.

## 9. Data Requirements

| Entity/Field | Purpose |
|--------------|---------|
| `Branch.BranchId`, `Name`, `Address`, `ContactDetails` | Core branch identity and contact information (ARCH-001 §13.1). |
| `Camera.CameraId`, `BranchId`, `Name`, `RtspUrl`, `Enabled` | One or more cameras associated with a branch. |
| `Device.DeviceId` (persistent) | Assigned on first activation; retained across all subsequent reactivations of the same logical device (FR-BRN-007, ADR-015). |
| `Device.BranchId` | Associates the Device with its branch; established at first activation. |
| `Device.ProtectedSharedSecret` | Backend-side recoverable-but-protected form of the current shared secret, used to authenticate Backend→Agent commands (ARCH-001 §13.3). |
| `Device.ActivationKeyHash` | Non-recoverable hashed form of the branch's current Activation Key. |
| `Device.ActivationKeyStatus` | Tracks unconsumed / consumed / invalidated state of the current Activation Key. |
| `Device.LastKnownAddress` | Most recently observed network address, supporting ASM-008. |
| `DeviceIdentity` (Jetson SQLite) | The Agent's persistent local record of its own Device ID and protected shared secret (ARCH-001 §13.2). |
| `ConfigCache` (Jetson SQLite) | The Agent's persistent local record of the most recently synchronized configuration, used at startup independent of Backend availability (ARCH-001 §13.2, §16.3). |

No binary media is introduced or referenced by this feature; only identity, credential, and configuration metadata are in scope (BR-006 is not engaged by FS-02).

## 10. API Contracts (Feature-Specification Level)

### 10.1 Branch Creation

| Aspect | Detail |
|--------|--------|
| Endpoint | `POST /api/v1/branches` |
| Auth required | Valid Admin JWT + active session (FS-01) |
| Request fields | Branch name, address, contact details, one or more camera configurations (name, RTSP URL) |
| Success response | Standard envelope; `data` contains created branch/camera details and the plaintext Activation Key |
| Success status code | 201 |
| Failure (invalid/incomplete submission) | Standard error envelope |
| Failure status code | 400 |
| Failure (no/invalid session) | Standard error envelope | 
| Failure status code | 401 |

### 10.2 Activation Key Regeneration

| Aspect | Detail |
|--------|--------|
| Endpoint | Backend endpoint under `/api/v1/branches/{id}` or `/api/v1/devices/{id}` scope for Activation Key regeneration (exact route is an Implementation Plan detail; ARCH-001 does not fix the exact path) |
| Auth required | Valid Admin JWT + active session (FS-01) |
| Request fields | None beyond the target branch/device identifier |
| Success response | Standard envelope; `data` contains the new plaintext Activation Key |
| Success status code | 200 |
| Effect | Previous Activation Key invalidated regardless of consumption state; new key generated, hashed, and stored unconsumed |
| Failure (branch/device not found) | Standard error envelope |
| Failure status code | 404 |
| Failure (no/invalid session) | Standard error envelope |
| Failure status code | 401 |

### 10.3 `POST /api/v1/activate`

| Aspect | Detail |
|--------|--------|
| Endpoint | `POST /api/v1/activate` |
| Auth required | None (exempt from Admin JWT authentication per FR-AUT-002/FS-01; authenticated instead by the Activation Key itself) |
| Request fields | Plaintext Activation Key |
| Success response | Standard envelope; `data` contains persistent Device ID, shared secret, and branch/camera/operational configuration |
| Success status code | 200 |
| Failure (key not found / invalid) | Standard error envelope; no Device ID/secret/config returned |
| Failure status code | 401 |
| Failure (key already consumed or invalidated) | Standard error envelope; no Device ID/secret/config returned; no change to any existing Device record |
| Failure status code | 401 |

All responses use the uniform response envelope defined in ARCH-001 §14.3 / ADR-009.

## 11. Security Rules

- The Activation Key is a one-time credential: consumed atomically on first successful use and never valid for a subsequent activation attempt until explicitly regenerated by the Admin (FR-BRN-004, BR-003).
- The Activation Key is stored by the Backend only in non-recoverable hashed form; the plaintext value exists only transiently at generation/regeneration time and in the corresponding single API response (ARCH-001 §15.5).
- The plaintext Activation Key is never written to application logs, on either the Backend or the Agent (ARCH-001 §15.6).
- The device shared secret is stored by the Backend in a recoverable but protected form (required for Backend→Agent command authentication in later features) and by the Agent under restrictive local file permissions; it is never written to application logs (ARCH-001 §13.3, §15.6).
- After successful activation, the persistent Device ID and shared secret — not the Activation Key — authenticate all subsequent Agent-to-Backend operational requests (NFR-SEC-002).
- Reactivation issues a new shared secret and invalidates the previous one; it does not issue a new Device ID for the same logical device (ARCH-001 §16.4, ADR-015).
- No certificates, mTLS, OAuth/OIDC, HMAC signing, or key-rotation scheduling beyond the approved reactivation secret replacement is introduced by this feature (ARCH-001 §15.6, §28.2).
- Branch creation and Activation Key regeneration are reachable only through the Admin-authenticated Dashboard API; no endpoint allows a Jetson Agent to create a branch or device record (FR-BRN-006, BR-002).

## 12. Validation Rules

- A branch-creation request must include branch name, address, contact details, and at least one camera configuration with a valid RTSP URL; an incomplete request is rejected before any Branch/Camera/Activation Key record is created.
- An activation request must include a non-empty Activation Key value; a request missing the key is rejected without a hash lookup.
- An Activation Key presented on `POST /api/v1/activate` must resolve, via hash comparison, to exactly one stored Activation Key record; no match results in rejection.
- A resolved Activation Key record must be in the unconsumed, non-invalidated state to proceed; any other state (consumed, invalidated by regeneration) results in rejection.
- Marking an Activation Key consumed, assigning/retaining the Device ID, and issuing the new shared secret must occur as a single atomic operation, so that a race between two concurrent activation attempts using the same key cannot result in two devices being issued credentials from one key.

## 13. Error Cases

| Case | Handling |
|------|----------|
| Branch-creation request missing required fields | 400; no Branch/Camera/Activation Key created |
| Branch-creation request without a valid Admin session | 401; request rejected before business logic |
| Activation request with an unknown/non-matching Activation Key | 401; no Device ID/secret/config returned |
| Activation request with an already-consumed Activation Key | 401; no change to the existing Device record |
| Activation request with a key invalidated by a subsequent regeneration | 401; no Device ID/secret/config returned |
| Key-regeneration request for a non-existent branch/device | 404 |
| Key-regeneration request without a valid Admin session | 401; request rejected before business logic |
| Jetson Agent attempts to call a branch/device-creation endpoint | 401 (no valid Admin session) or 404 (no such Agent-reachable endpoint exists); in all cases, no branch/device is created by the Agent |

## 14. Acceptance Criteria

| # | Acceptance Criterion | Traces To |
|---|------------------------|-----------|
| AC-1 | A branch created via the Dashboard with name, address, contact details, and camera configuration(s) produces a persisted Branch record with associated Camera record(s). | FR-BRN-001 |
| AC-2 | Branch creation produces a unique Activation Key, shown in plaintext to the Admin exactly once, at generation time. | FR-BRN-002 |
| AC-3 | A Jetson Agent presenting a valid, unconsumed Activation Key to `POST /api/v1/activate` receives its branch/camera/operational configuration and activates successfully. | FR-BRN-003 |
| AC-4 | An Activation Key is marked consumed after its first successful use, and a subsequent activation attempt using the same key is rejected. | FR-BRN-004 |
| AC-5 | Regenerating a branch's Activation Key invalidates the previous key such that a subsequent activation attempt using the old key fails, regardless of whether the old key had already been consumed. | FR-BRN-005 |
| AC-6 | No API path exists by which a Jetson Agent can create or self-register a new branch. | FR-BRN-006 |
| AC-7 | A device's persistent identity (Device ID) is retained across restarts and reconnections, and across a reactivation using a newly regenerated key, such that the Backend correlates all communications to the correct device. | FR-BRN-007 |
| AC-8 | No device can be activated against a branch that does not already exist. | BR-002 |
| AC-9 | An Activation Key can be consumed at most once; reactivating or replacing a device requires a newly regenerated key. | BR-003 |
| AC-10 | Physical installation and Activation Key configuration on the Jetson device are performed out-of-band by an installer; the system provides no automated remote provisioning path. | ASM-006 |
| AC-11 | The Backend can resolve a working network address for a registered device following activation and subsequent operational contact. | ASM-008 |
| AC-12 | Initial activation uses a one-time Activation Key; upon success, the Backend issues a persistent Device ID and shared secret that authenticate subsequent Agent-to-Backend operational requests. | NFR-SEC-002 |

## 15. Test Scenarios

### Branch Creation

- T-01: Admin submits a complete branch-creation request → Branch and Camera record(s) created → unique Activation Key generated and shown once in the response.
- T-02: Admin submits a branch-creation request missing a required field → rejected (400); no Branch/Camera/Activation Key created.

### First Activation

- T-03: Agent calls `POST /api/v1/activate` with the valid, unconsumed key from T-01 → activation succeeds → Device ID, shared secret, and configuration returned → key marked consumed.
- T-04: Agent repeats the same activation call with the now-consumed key → rejected (401); no change to the Device record created in T-03.

### Invalid Key

- T-05: Agent calls `POST /api/v1/activate` with a key that does not match any stored record → rejected (401); no Device record created.

### Key Regeneration

- T-06: Admin regenerates the Activation Key for a branch whose original key was never consumed → previous key invalidated → new key generated and shown once.
- T-07: Agent attempts activation using the now-invalidated original key from T-06 → rejected (401).
- T-08: Agent activates using the new key from T-06 → succeeds, following the same behavior as T-03.

### Reactivation

- T-09: A branch already has an activated Device (from T-03/T-08). Admin regenerates the Activation Key. Agent (new or reset) activates using the new key → the returned Device ID matches the branch's existing Device ID (retained, not reassigned) → a new shared secret is issued, replacing the previous one.
- T-10: Historical alert/health records associated with the retained Device ID (created in a prior feature's test data, referenced here only for correlation) remain queryable and correlated to that same Device ID after reactivation.

### Agent Self-Registration Attempt

- T-11: An unauthenticated request (or a request bearing only a Device shared secret, not an Admin session) attempts to reach the branch-creation endpoint → rejected; no branch is created.

### Agent Startup After Previous Activation

- T-12: Agent restarts with an existing local `DeviceIdentity` and `ConfigCache` → Agent does not call `POST /api/v1/activate` again → Agent resumes operation using its existing Device ID and shared secret.

## 16. Out of Scope

Consistent with SRS §11, CON-007, and ARCH-001 §1.5/§28.2, this feature explicitly excludes:

- Device self-registration or any Agent-initiated branch/device creation.
- Multiple Jetson devices per branch.
- Remote or zero-touch device provisioning; Activation Key configuration on the device remains an out-of-band, installer-performed action (ASM-006).
- Certificates, mTLS, OAuth/OIDC, HMAC signing, or any key-rotation scheduling beyond the single shared-secret replacement that occurs on reactivation.
- Exposing the plaintext Activation Key at any point other than the initial generation or regeneration response.
- Recoverable plaintext storage of the Activation Key on the Backend.
- Starting or supervising the DeepStream pipeline; activation returns configuration only, and Agent bootstrap/DeepStream supervision is specified in a later Feature Specification.
- The detailed behavior of ongoing Agent→Backend operational endpoints (heartbeat, alerts, sync, config retrieval) that use the Device ID/shared secret issued here; those are specified in their own respective Feature Specifications.

## 17. Traceability Matrix

| Requirement/Decision | Realized In This Feature |
|------------------------|----------------------------|
| FR-BRN-001 | §5.1, §6, §10.1, AC-1 |
| FR-BRN-002 | §5.1, §5.2, §6, §10.1, §11, AC-2 |
| FR-BRN-003 | §5.3, §6, §10.3, AC-3 |
| FR-BRN-004 | §5.3, §5.5, §6, §11, §13, AC-4 |
| FR-BRN-005 | §5.6, §6, §10.2, AC-5 |
| FR-BRN-006 | §5.1, §6, §8, §11, AC-6 |
| FR-BRN-007 | §5.3, §5.7, §5.8, §9, §11, AC-7 |
| BR-002 | §3, §6, §11, AC-8 |
| BR-003 | §5.3, §5.5, §5.6, §6, §11, AC-9 |
| ASM-006 | §2, §5.3, §8, AC-10 |
| ASM-008 | §6, §9, AC-11 |
| NFR-SEC-002 | §5.3, §5.7, §6, §11, AC-12 |
| ARCH-001 §16.4, ADR-015 | §5.7, §6, §9, §11 (Device ID retention, secret rotation) |
| ARCH-001 §13.2, ADR-004 | §5.3, §5.8, §9 (DeviceIdentity, ConfigCache) |
| ARCH-001 §13.3, §15.5 | §6, §11 (credential storage) |
| ADR-002 (amended) | §5.3, §10.3, §11 (Activation Key → Device ID/shared-secret exchange) |

## 18. Open Implementation Details (Deferred to Implementation Plan)

The following are implementation parameters, not requirements, and are intentionally left open for the Implementation Plan phase:

- Exact route/path for the Activation Key regeneration endpoint (e.g., under `/api/v1/branches/{id}` vs. `/api/v1/devices/{id}`).
- Exact Activation Key format/length and the specific hashing algorithm used to store it.
- Exact fields comprising "contact details" for a branch.
- Exact mechanism/timing by which the Backend updates `Device.LastKnownAddress` (e.g., derived from the activation request's originating address vs. a later operational contact).
- Exact concurrency-control mechanism (e.g., database transaction isolation level, unique constraint) used to guarantee atomic Activation Key consumption under concurrent activation attempts.
