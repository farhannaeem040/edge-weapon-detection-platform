# Implementation Plan: Device Reactivation Security (Immediate Credential Revocation + Agent Lock)

| Field | Value |
|-------|-------|
| Plan ID | IP-05 |
| Title | Device Reactivation Security — immediate shared-secret revocation, Agent revocation-detection, and operational lock |
| Status | Draft — awaiting approval (amended 2026-07-21, Agent-lock expansion) |
| Realizes | FS-02 (amended); SRS FR-BRN-005 / NFR-SEC-002 / new FR-BRN-008 / NFR-SEC-005 (amended/added); ARCH-001 §14.1 / §16 / ADR-015 / new ADR-017 (amended/added) |
| Governing Documents | SRS-001, ARCH-001, FS-02 (all amended for this change), Engineering Principles, Development Workflow |
| Depends On | IP-01 (complete), IP-02 (T-31–T-40 complete; T-41 real-Jetson activation/restart/reboot evidence passed — see IP-02 §21/memory), IP-03/IP-04 (complete) |
| Task ID Range | **T-48 – T-69** (T-48 delivered as commit `a8ad221`; T-49–T-69 extend the range for the Agent-lock work) |
| Owner | Farhan Naeem |
| Explicitly Excluded | Heartbeat / health monitoring / Online-Offline connectivity (Offline stays reserved); DeepStream, detection, alerts, commands, siren, WebRTC **implementation** (only the operational-lock hook they will use is added); last-seen/health telemetry from the validation endpoint; any `.claude/settings.json` / `.mcp.json` change; automatic activation retries. |

> **Amendment 2026-07-21 (Agent-lock expansion).** **Superseded assumption:** the earlier IP-05 stated "no Agent production change is expected — the Agent contract is unchanged." That is now **withdrawn.** **Reason:** Backend revocation alone prevents *future* authenticated requests, but a **currently running** Agent does not know its secret was revoked and would keep operating on a dead credential. The Agent therefore gains a credential-**validation** mechanism (detect-only; never distributes a key), a persistent local **locked** state, and an operational-lock coordinator. T-48 (`a8ad221`, domain status + revocation transition + `CanAuthenticate()`) remains valid and is **not** reverted or rewritten.

---

## 1. Problem and Objective

Regenerating the Activation Key of an already-activated device is a **security-first credential reset**: the Backend immediately revokes the shared secret, preserves the permanent `DeviceId`, and sets `ActivationStatus = ReactivationRequired` (T-48 domain + T-50 service). But the **running Agent** must also **detect** the confirmed revocation, **lock** its operational functionality, persist that locked state across restarts/reboots, and resume **only** after an operator **manually** provisions the new Activation Key via `set-activation-key.sh` (single-disclosure, out-of-band). The Agent must **never** fetch, request, receive, or log a replacement key, and must **never** auto-retry activation.

**Honest limitation (no instantaneous remote shutdown).** If the Jetson is disconnected when the Admin regenerates the key, the Agent cannot learn of the revocation until connectivity returns and its next validation request receives a **confirmed** credential rejection. This is polling-based, bounded by one interval + timeout; it is not a remote kill switch, and the plan does not claim one.

## 2. Grounding in the Delivered Code

| Fact | Evidence | Consequence |
|------|----------|-------------|
| Device auth headers are `X-Device-Id` + `X-Device-Secret` | ARCH §14.1 (heartbeat/alerts/config all use them) | The new validation endpoint uses the same headers — no new auth scheme. |
| `IDeviceSecretProtector` has `Protect` **and** `Unprotect` | `IDeviceSecretProtector.cs` | Backend recovers the stored secret and constant-time-compares it to the presented one. |
| `Device.CanAuthenticate()` gates status+secret (T-48) | `Device.cs` (a8ad221) | Backend validation reuses it as the state precondition before the crypto compare. |
| Agent SQLite is **schema v1**, forward-only versioned | `persistence/schema.py` (`CURRENT_SCHEMA_VERSION = 1`) | A **v2** migration appends a step; existing rows preserved. |
| Agent stores the plaintext secret under `0600` file perms (D-4) | IP-02 §10.1 | The Agent can present `X-Device-Secret` from `DeviceIdentity`. |
| Agent settings are `WDA_`-prefixed, validated, immutable | `config/settings.py` | Add `WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS` there. |
| Agent activation is **one-shot, never retried** | IP-02 §14 | The validation loop is **not** activation; it never calls `/activate`. |
| Status stored as enum string `nvarchar(32)`; secret nullable | `DeviceConfiguration` | No Backend column migration for status. |

### 2.1 Authentication-enforcement rule (binding)

Every device-authentication path must require **both** `ActivationStatus == Activated` **and** a present, valid protected shared secret, checking **status first** — so a `ReactivationRequired`/`Unactivated` device is rejected **even if** inconsistent data leaves a secret present (T-48 `CanAuthenticate()`). The credential-state guard **precedes**, and never replaces, cryptographic secret verification.

## 3. Concurrency Design and Failure Contract (AC-18)

A **filtered unique index** `UNIQUE (DeviceRecordId) WHERE Status = 'Unconsumed'` makes "at most one unused key per device" a schema invariant. **No last-writer-wins is claimed.** Rule: exactly one concurrent regeneration commits a valid `Unconsumed` key; competing requests fail cleanly (roll back, return **no** key), mapped to **`409 Conflict`** `errorCode: ACTIVATION_KEY_REGENERATION_CONFLICT`; afterward exactly one valid `Unconsumed` key exists. The transaction invalidates+flushes old `Unconsumed` keys **before** inserting the new one (per-statement index check).

## 4. State Machines

### 4.1 Backend `Device.ActivationStatus`
```
Unactivated ──activate──▶ Activated ──admin regenerates key──▶ ReactivationRequired ──activate(new key)──▶ Activated
```
`ReactivationRequired` regenerated again → stays `ReactivationRequired` (idempotent, `DeviceId` preserved, secret null). `Unactivated` regenerated → stays `Unactivated`.

### 4.2 Backend `Device` credential-state invariants (T-48)
| Status | `DeviceId` | `ProtectedSharedSecret` |
|--------|-----------|--------------------------|
| `Unactivated` | null | null |
| `Activated` | present | present |
| `ReactivationRequired` | present | null |

### 4.3 Agent local operational state (NEW — persisted, schema v2 §7)
```
Operational ──confirmed 401 INVALID_DEVICE_CREDENTIALS──▶ ReactivationRequired(locked, secret cleared) ──manual reactivation(new key)──▶ Operational
```
`Operational`: `DeviceId` + secret present, operational components may run. `ReactivationRequired` (locked): secret **cleared** (`NULL`), `DeviceId`/`ActivatedAt`/`LastActivatedAt`/record retained; operational components stopped/prevented; validation loop stopped; no auto-activation; **survives restart/reboot**.

## 5. Backend Credential-Validation Endpoint (approved contract — OI-A)

A dedicated, **detect-only** endpoint. It never returns, rotates, or distributes a key or secret; it is not a heartbeat and updates no status/config/health/last-seen/Online-Offline state.

| Aspect | Detail |
|--------|--------|
| Route | **`POST /api/v1/device/credentials/validate`** |
| Auth | Admin-JWT-exempt (`[AllowAnonymous]`, like `/activate`); **device-authenticated** by headers |
| Headers | `X-Device-Id: <permanent public Device ID>`, `X-Device-Secret: <current private shared secret>` |
| Body | none |
| Success | `200` — the repository's existing standard success envelope; since `ApiResponse` carries a `data` member, `{"success": true, "data": null}` (no key, secret, status, config, health, Online/Offline, or replacement credential) |
| Failure | `401` — `{"success": false, "message": "The device credentials are invalid.", "errorCode": "INVALID_DEVICE_CREDENTIALS"}` |

Uses the repository's existing envelope conventions exactly — **no new envelope style**.

**Backend authorization succeeds only when all hold:** the `X-Device-Id` identifies an existing Device; `ActivationStatus == Activated`; `ProtectedSharedSecret` present; the presented `X-Device-Secret` cryptographically matches. Order (design req 1): locate Device by public `DeviceId` → require `Device.CanAuthenticate()` **first** → only then `Unprotect` the stored secret → **constant-time** compare (`CryptographicOperations.FixedTimeEquals` over UTF-8 bytes) → never log either value.

**Uniform `401`** for **every** rejection — missing/malformed/unknown Device ID, missing/incorrect/revoked shared secret, missing stored protected secret, status `Unactivated`, status `ReactivationRequired`, any other credential-state rejection. The response **never** reveals which condition failed and **never** returns `ReactivationRequired` details (the Admin Dashboard reads explicit status through its authenticated management endpoints, §10.3). The endpoint **should not normally return `403`**.

**Confirmed-revocation signal (design req, OI-A).** Only a `401` from *this* endpoint carrying `errorCode == INVALID_DEVICE_CREDENTIALS` is a confirmed revocation/invalid-credential signal that may **persistently lock** the Agent. Any `403`/`404`/`408`/`429`/`5xx`/timeout/DNS/connection-refusal/network/malformed/unexpected-code outcome is ambiguous or infrastructural and must **not** lock — this prevents a reverse-proxy, authorization-policy, deployment, or transient server error from permanently disabling the Jetson.

## 6. Agent Validation Polling (interval / timeout)

- New validated setting **`WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS`** (default **30**; **integer > 0**), added to `AgentSettings`. The per-request timeout continues to use `WDA_HTTP_TIMEOUT_SECONDS`.
- While **Operational**, a single asyncio task issues **one** validation request per interval — **no overlapping requests**, no burst; it cancels cleanly on shutdown. **This loop is not activation** and never calls `/activate`; activation is never automatically retried.

**Response classification (OI-A — only a confirmed `401` locks):**

| Outcome | Agent action |
|--------|--------------|
| `200` | remain Operational; continue normal functionality |
| **`401` with `errorCode == INVALID_DEVICE_CREDENTIALS`** (confirmed) | atomically persist `OperationalState = ReactivationRequired` **and** `ProtectedSharedSecret = NULL` (OI-B); stop/prevent operational functionality; preserve `DeviceId`/`ActivatedAt`/`LastActivatedAt`/identity record; **stop the validation loop** (OI-C) until manual reactivation |
| `403` / `404` / `408` / `429` / `5xx` / timeout / DNS / connection refused / Tailscale outage / malformed / unexpected code | **ambiguous or infrastructural** — do **not** lock, do **not** erase credentials; keep the current operational state; retry at the next interval |

**Connected-revocation timing (design req 4):** when connected and the Backend is healthy, the Agent detects revocation within **one validation interval + one request timeout** — a bounded delay, **not** instantaneous shutdown. "Offline" is never used for revocation.

**While locked (OI-C):** the validation loop is stopped; the Agent does not resubmit a revoked/cleared secret, does not auto-activate, does not retrieve or poll for a replacement key, and waits for manual operator action.

## 7. Local Persistent-State Design (approved — OI-B, security-first clearing)

The current v1 `DeviceIdentity` has `ProtectedSharedSecret TEXT NOT NULL`. OI-B requires clearing the revoked secret to `NULL`, so schema **v2** makes that column **nullable** and adds a persisted `OperationalState`, via the standard SQLite forward-only **table-rebuild** (SQLite cannot drop a `NOT NULL` in place).

```sql
-- Schema version 2 (forward-only; existing rows preserved; atomic)
CREATE TABLE DeviceIdentity_v2 (
    SingletonGuard        INTEGER PRIMARY KEY CHECK (SingletonGuard = 1),
    DeviceId              TEXT    NOT NULL,
    ProtectedSharedSecret TEXT    NULL,                       -- nullable in v2 (was NOT NULL)
    ActivatedAt           TEXT    NOT NULL,
    LastActivatedAt       TEXT    NOT NULL,
    OperationalState      TEXT    NOT NULL DEFAULT 'Operational'
        CHECK (OperationalState IN ('Operational','ReactivationRequired'))
);
INSERT INTO DeviceIdentity_v2 (SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, OperationalState)
    SELECT SingletonGuard, DeviceId, ProtectedSharedSecret, ActivatedAt, LastActivatedAt, 'Operational' FROM DeviceIdentity;
DROP TABLE DeviceIdentity;
ALTER TABLE DeviceIdentity_v2 RENAME TO DeviceIdentity;
-- then: UPDATE SchemaVersion SET Version = 2;
```

**Schema-v2 invariants** (mirror the Backend §4.2, enforced by the repository + `CHECK`):

| OperationalState | `DeviceId` | `ProtectedSharedSecret` |
|------------------|-----------|--------------------------|
| `Operational` | present | present |
| `ReactivationRequired` | present | `NULL` |

Behaviour:
- **First activation:** store `DeviceId` + secret, `OperationalState = 'Operational'`.
- **Confirmed `401 INVALID_DEVICE_CREDENTIALS`:** in **one transaction** set `OperationalState = 'ReactivationRequired'` **and** `ProtectedSharedSecret = NULL`; **preserve** `DeviceId`, `ActivatedAt`, `LastActivatedAt`, and the identity record. The revoked secret is deleted locally — there is no reason to keep an invalidated credential.
- **Normal restart while `ReactivationRequired`, no new key:** remain locked; do **not** validate using a cleared credential; do **not** start operational components; do **not** fall back to offline operation on the revoked credential.
- **Manual reactivation (new key):** exactly one `/activate`; **verify returned `DeviceId` equals stored**; then in one transaction store the new secret, set `OperationalState = 'Operational'`, preserve `ActivatedAt`, advance `LastActivatedAt`; delete the key file **only after** persistence commits. **If persistence fails, keep the key file and remain locked.**

**Migration/rollback strategy:** the rebuild runs inside a single transaction (begin → create v2 → copy → drop/rename → set `SchemaVersion = 2` → commit). Any failure rolls back, leaving the original **v1** database intact and readable by the v1 code; the migrator remains forward-only and idempotent (a database already at v2 is a no-op). Covered by transactional migration tests (T-58).

## 8. Startup Decision Table

| Branch | Stored identity | Key file present | Local state | Action |
|--------|-----------------|------------------|-------------|--------|
| **A** | no | yes | — | First activation; on success → Operational |
| **B** | yes | yes | any | Manual reactivation; **exactly one** `/activate`; returned `DeviceId` must equal stored; on success clear lock → Operational |
| **C** | yes | no | `ReactivationRequired` | Start control process **locked**; do **not** validate using a cleared credential; no operational components; **no** auto-activation |
| **D** | yes | no | `Operational` | Attempt **one** validation before starting operational components when Backend communication succeeds: `200` → start Operational; **`401 INVALID_DEVICE_CREDENTIALS`** → clear secret, persist `ReactivationRequired`, remain locked; timeout/network/`5xx`/malformed/`403`/`404`/unexpected → apply the offline-start policy and **remain Operational** |

Branch D reconciles ARCH-001 §16.3 + NFR-REL-001: **network failure alone must not stop local operation**, but **confirmed credential revocation must**. On any ambiguous/infrastructural outcome at startup the Agent starts operationally from local state (offline-start preserved) and the polling loop (§6) will lock it if/when a confirmed `401 INVALID_DEVICE_CREDENTIALS` later arrives.

## 9. Offline-versus-Revoked Decision Table

| Signal | Interpretation | Effect |
|--------|----------------|--------|
| `200` | credentials valid | stay/enter Operational |
| **`401 INVALID_DEVICE_CREDENTIALS`** | **revoked / invalid credential** (confirmed) | clear secret, lock → `ReactivationRequired`, persist, stop operational, stop validation loop |
| `403` / `404` / `408` / `429` | authorization-policy / routing / infrastructural | **ambiguous** — stay Operational; retry next interval |
| timeout / DNS / conn refused / TLS/Tailscale down | server unreachable | stay Operational (or offline-start); retry next interval |
| `5xx` / malformed body / unexpected status/code | server fault | stay Operational; retry next interval |

Only a **confirmed `401 INVALID_DEVICE_CREDENTIALS`** from the dedicated endpoint locks the Agent. Everything else — including `403` — is ambiguous and never locks it, so a reverse-proxy/authorization/deployment/transient error cannot permanently disable the Jetson.

## 10. Operational-Lock Interface (coordinator)

DeepStream, detection, alert submission/sync, live-stream authorization, remote command execution, and siren are **not implemented yet** and are **not** implemented here. Instead, introduce a small **operational-state coordinator** exposing an explicit contract future components must obey (design req 7):

- **`CanRunOperationalComponents`** — read-only predicate (true only when local state is `Operational`);
- **`EnterReactivationRequiredAsync()`** — invoked by the validation loop / startup on a confirmed `401`; atomically persists the cleared-secret `ReactivationRequired` state (§7) and signals registered components to stop;
- **`MarkOperationalAfterReactivationAsync()`** — invoked after a successful manual reactivation persists; flips to `Operational` and permits components to start.

Every future operational component **must** check `CanRunOperationalComponents` before starting and **must** stop when the coordinator enters `ReactivationRequired`.

For this milestone, tests use a **fake** operational component proving it: starts only when `Operational`; stops after a confirmed revocation; cannot restart while `ReactivationRequired`; a **network failure does not stop it**; resumes only after successful manual-reactivation persistence.

### 10.1 Secret/header safety (design req 2)
- `X-Device-Secret` is structurally redacted from application logs on both tiers (Backend logging + the Agent's existing redactor, which already redacts `secret`/`authorization`-shaped fields — extend it to the header name).
- Validation exceptions never include request-header values; no test prints the supplied or stored secret; secret equality is asserted as a boolean only.
- The Nginx reverse proxy (central stack) must not explicitly log the `X-Device-Secret` header (verify the access-log format carries no custom header capture).

## 11. Implementation Tasks

**T-48 — Domain: `ReactivationRequired` + `RequireReactivation()` + `CanAuthenticate()` + invariants — DELIVERED (`a8ad221`).** Preserved; not reused, reverted, or rewritten.

| Task | Scope |
|------|-------|
| **T-49** | Persistence: filtered unique `Unconsumed` index + migration + **pre-migration duplicate guard** (throws clearly; no arbitrary key selection) |
| **T-50** | Backend service: atomic regeneration = invalidate+flush old keys → (if Activated/ReactivationRequired) `RequireReactivation()` → insert new key → commit; plaintext returned **only after commit**; conflict → typed result; Unactivated stays null/null |
| **T-51** | Backend **device-credential validation service**: `CanAuthenticate()` gate + `Unprotect` + constant-time compare; uniform result; never returns/rotates a secret |
| **T-52** | Backend **validation API endpoint** `POST /api/v1/device/credentials/validate` (device-authenticated, `[AllowAnonymous]` for Admin-JWT) → `200 {success,data:null}` / uniform `401 {..."The device credentials are invalid.", errorCode:"INVALID_DEVICE_CREDENTIALS"}`; wire the regeneration `409` conflict; surface `ReactivationRequired` in the Admin read DTOs (comments only) |
| **T-53** | Backend unit + **real-SQL integration** tests: regeneration/revocation atomicity & rollback, concurrency, validation success/uniform-failure (incl. ReactivationRequired-with-stray-secret rejected), reactivation retains DeviceId, consumed/superseded → 401 |
| **T-54** | Angular: `ReactivationRequired` model + badge state |
| **T-55** | Angular: destructive warning (only when Activated/ReactivationRequired) + explicit confirm + post-success branch refresh + `409` benign-retry (no key) |
| **T-56** | Agent settings: `WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS` (default 30, >0) validated |
| **T-57** | Agent: authenticated **validation HTTP client** — one `POST /api/v1/device/credentials/validate` with `X-Device-Id`/`X-Device-Secret`; typed outcomes where **only `401 INVALID_DEVICE_CREDENTIALS` = confirmed rejection** and `403`/`404`/`408`/`429`/`5xx`/timeout/transport/malformed = ambiguous; never sends or receives a key; `X-Device-Secret` redacted from logs |
| **T-58** | Agent: **local credential/operational state** + SQLite **schema v2** migration (§7) — **make `ProtectedSharedSecret` nullable** (table-rebuild), add persisted `OperationalState`; **transactional migration + rollback tests** (v1 preserved on failure, idempotent); repository ops: **clear secret + set `ReactivationRequired`** atomically on confirmed revocation, and **persist new secret + `Operational`** atomically on reactivation, always retaining `DeviceId`/`ActivatedAt` |
| **T-59** | Agent: **validation polling lifecycle** — one non-overlapping request per interval while Operational; confirmed `401 INVALID_DEVICE_CREDENTIALS` → lock+persist and **stop the loop** (OI-C); ambiguity → retry next interval; clean cancellation on shutdown; never activation |
| **T-60** | Agent: **operational-state coordinator** (`CanRunOperationalComponents`, `EnterReactivationRequiredAsync()`, `MarkOperationalAfterReactivationAsync()`) + lock transition (§10) with a fake operational component in tests (incl. network-failure-does-not-stop-it) |
| **T-61** | Agent: **startup/offline policy** — branches A–D (§8), reconciled with ARCH §16.3 / NFR-REL-001; confirmed-revocation-locks, outage-does-not |
| **T-62** | Agent **simulated-Backend** tests: revocation detection, lock, restart-stays-locked, manual reactivation clears lock, offline-start, no auto-retry, no secret in logs |
| **T-63** | Agent **real-Backend contract** tests: activate → regenerate (revoked, ReactivationRequired) → validation returns confirmed 401 → reactivate with new key (same DeviceId, new secret) → validation 200 |
| **T-64** | systemd / deployment update: env template gains the interval; README manual-recovery + lock behaviour; `install.sh` ownership/mode fix (T-41 carry-over) |
| **T-65** | Central Docker rebuild + migration verification (Backend index migration applies cleanly in the Compose stack) |
| **T-66** | Real-Jetson **connected** revocation test: regenerate in Dashboard → Agent detects within one interval+timeout → locks; operational hook stopped |
| **T-67** | Real-Jetson **offline-then-reconnect** revocation test: regenerate while Agent disconnected → on reconnect, next validation confirms rejection → locks (honest bounded detection) |
| **T-68** | Real-Jetson **manual reactivation** via `set-activation-key.sh` → one `/activate`, same DeviceId, rotated secret, lock cleared, key file consumed after persistence; then normal restart + reboot stay Operational without re-activation |
| **T-69** | Documentation + MAC evidence: FS-02/SRS/ARCH/ADR/IP-02/IP-05, root+agent+deployment READMEs; AC evidence table; no key/secret recorded |

## 12. Acceptance Criteria → Task Traceability

| AC | Summary | Task(s) |
|----|---------|---------|
| AC-1 | First activation still works | T-50, T-53 |
| AC-2 | Regeneration preserves `DeviceId` | T-48, T-50, T-53 |
| AC-3 | Regeneration immediately revokes the secret (Backend) | T-48, T-50 |
| AC-4 | Status atomically `Activated → ReactivationRequired` | T-48, T-50 |
| AC-5 | All previously unused keys invalidated | T-50, T-53 |
| AC-6 | Only one unused valid key after regeneration | T-49, T-50 |
| AC-7 | Failed regeneration leaves prior state unchanged | T-50, T-53 |
| AC-8 | Dashboard destructive warning before regeneration | T-55 |
| AC-9 | Dashboard shows *Reactivation required* (not Activated/Offline) | T-52, T-54, T-55 |
| AC-10 | Old secret never authenticates again (validation endpoint rejects it) | T-51, T-52, T-53, T-63 |
| AC-11 | New key reactivates exactly once | T-53, T-63, T-68 |
| AC-12 | Reactivation retains `DeviceId` + original `ActivatedAt` | T-53, T-58, T-62, T-63, T-68 |
| AC-13 | Reactivation rotates secret + advances `LastActivatedAt` | T-53, T-58, T-62 |
| AC-14 | Successful reactivation returns to `Activated`/Operational | T-53, T-61, T-63, T-68 |
| AC-15 | Consumed key → `401 INVALID_ACTIVATION_KEY` | T-53, T-63 |
| AC-16 | Superseded key → same uniform `401` | T-53, T-63 |
| AC-17 | Concurrent regeneration cannot leave multiple valid keys | T-49, T-53 |
| AC-18 | No key/secret in logs, exceptions, URLs, argv, or committed files | all tasks, T-69 |
| AC-19 | Branch/Camera/historical records stay linked to same `DeviceId` | T-50, T-53, T-63 |
| AC-20 | Restart/reboot after reactivation loads identity without re-activating | T-62, T-68 |
| **AC-21** | Generating a new key immediately revokes the old shared secret on the Backend | T-50, T-53 |
| **AC-22** | The replacement key is never sent automatically to the Agent; provisioned only via `set-activation-key.sh` | T-57, T-59, T-63, T-64 |
| **AC-23** | The validation endpoint never returns a key or shared secret | T-51, T-52, T-53 |
| **AC-24** | A connected Agent detects confirmed revocation within one configured interval + timeout | T-59, T-66 |
| **AC-25** | The Agent persists `ReactivationRequired` locally; a restart cannot clear the lock | T-58, T-61, T-62, T-68 |
| **AC-26** | Operational components stop / remain prevented while locked | T-60, T-62, T-66 |
| **AC-27** | A transport failure does **not** falsely revoke; ordinary Backend outage continues offline operation | T-59, T-61, T-62 |
| **AC-28** | An Agent disconnected during regeneration detects revocation after reconnection | T-59, T-67 |
| **AC-29** | Manual reactivation retains DeviceId, rotates secret, clears the lock; components resume only after successful persistence; key file deleted only after persistence; no auto-retry | T-58, T-61, T-62, T-68 |

## 13. Non-Goals / Constraints
No heartbeat/Online-Offline; no DeepStream/detection/alerts/commands/siren/WebRTC implementation (only the lock hook); validation endpoint carries no health/last-seen telemetry; no auto activation retries; no push; no `.claude/settings.json`/`.mcp.json` change.

## 14. Open Items (resolved 2026-07-21)
- **OI-A — resolved:** route `POST /api/v1/device/credentials/validate`; success `{success:true,data:null}`; uniform `401 INVALID_DEVICE_CREDENTIALS` ("The device credentials are invalid."); **only** that `401` locks (§5, §6).
- **OI-B — resolved:** on confirmed revocation the Agent **clears** the local secret (`ProtectedSharedSecret = NULL`) and persists `ReactivationRequired` atomically; schema **v2** makes the column nullable via table-rebuild (§7).
- **OI-C — resolved:** while locked, the validation loop **stops**; recovery is manual only (§6, §10).

---

*IP-05 — amended 2026-07-21 (Agent-lock expansion). T-48 delivered (`a8ad221`); T-49–T-69 not started; awaiting approval before T-49.*
