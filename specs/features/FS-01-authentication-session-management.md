# Feature Specification: Authentication & Session Management

| Field | Value |
|-------|-------|
| Feature ID | FS-01 |
| Title | Authentication & Session Management |
| Status | Final |
| Related SRS Requirements | FR-AUT-001, FR-AUT-002, FR-AUT-003, NFR-SEC-001, BR-001 |
| Related Architecture Sections | ARCH-001 §10.1 (AuthController/AuthService), §13.1 (AdminUser, AdminSession), §14.1 (Backend API), §15.1–15.3 (Security Architecture, Password Handling, Logout/Session Invalidation) |
| Related ADRs | ADR-002 (amended) — Security Architecture; ADR-009 — API Design; ADR-013 — Dashboard Session Revocation |
| Owner | Farhan Naeem |
| Dependencies | None (foundational feature; all other Dashboard-facing features depend on this one) |

---

## 1. Purpose and Scope

This feature specifies the behavior required for a single Admin user to authenticate into the Angular Dashboard, for that authentication to gate all protected Dashboard views and Dashboard-initiated Backend API operations, and for a session to be reliably invalidated on logout.

This feature realizes FR-AUT-001–003 and NFR-SEC-001 as architected in ARCH-001 §15 (JWT Bearer authentication with server-side session revocation via `AdminSession`). It covers only the single-Admin-account model defined by BR-001; it does not introduce multiple users, roles, or permission levels.

**Authentication boundary.** FS-01 governs authentication for the Angular Dashboard and for Dashboard-initiated, user-facing ASP.NET Core Backend API operations only. It does not govern Jetson Agent activation or Agent↔Backend operational authentication. Jetson Agent activation is exempt from Admin JWT authentication under FR-AUT-002, but the Activation Key exchange and the Device ID/shared-secret mechanism that authenticate ongoing Agent↔Backend communication are architecturally distinct (ARCH-001 §15.1, §16) and are specified in later Feature Specifications, not here. The Admin JWT session defined by this feature never authenticates Jetson Agent API communication.

## 2. Actors

| Actor | Description |
|-------|--------------|
| Admin User | The single authenticated account representing both the System Administrator and Security Operator conceptual roles (BR-001). |
| ASP.NET Core Backend | Issues, validates, and revokes authentication sessions. |
| Angular Dashboard | Presents the login form, stores the issued JWT, attaches it to outgoing API requests, and reacts to authentication failures. |

## 3. Preconditions

- One provisioned `AdminUser` account exists for the prototype, provisioned out-of-band (SRS ASM-005; not created by this feature — no registration flow exists).
- The Backend is reachable by the Dashboard over the local network.

## 4. Functional Behavior

| Behavior | Requirement Basis |
|----------|---------------------|
| The Admin can log in with a `credentialIdentifier` and password to obtain an authenticated session. | FR-AUT-001 |
| All protected Dashboard views and Dashboard-initiated Backend API operations require a valid, non-revoked session; only `POST /api/v1/auth/login` does not require an existing Admin session. Jetson Agent activation is separately exempt from Admin JWT authentication under the SRS, but is outside FS-01's own behavior. | FR-AUT-002 |
| The Admin can log out, immediately invalidating the current session such that it cannot be used again, including by a copy of the token made before logout. | FR-AUT-003 |
| An expired JWT is rejected regardless of session-revocation state. | NFR-SEC-001, ARCH-001 §15.3 |
| A revoked (logged-out) session is rejected even if the JWT signature and expiry are otherwise valid. | FR-AUT-003, ARCH-001 §15.3 |
| Invalid credentials at login are rejected without issuing a session. | FR-AUT-001 |
| Direct navigation to a protected Dashboard route without a valid session redirects to login rather than rendering protected content. | FR-AUT-002, NFR-SEC-001 |

## 5. Detailed Workflows

### 5.1 Successful Login

1. Admin submits credentials via the Dashboard login form.
2. Dashboard calls `POST /api/v1/auth/login`.
3. Backend's `AuthController` delegates to `AuthService`, which verifies the credential against the stored salted password hash (`AdminUser`).
4. On success, `AuthService` issues a JWT containing a unique session identifier (`jti`) and an expiry, and creates a corresponding `AdminSession` record (issued, not revoked).
5. Backend returns the JWT to the Dashboard in the standard response envelope.
6. Dashboard stores the JWT and attaches it as a Bearer token on subsequent API requests.

### 5.2 Failed Login (Invalid Credentials)

1. Admin submits credentials via the Dashboard login form.
2. Dashboard calls `POST /api/v1/auth/login`.
3. `AuthService` fails to verify the credential against the stored hash.
4. Backend returns an authentication failure response; no JWT is issued; no `AdminSession` record is created.
5. Dashboard displays an authentication failure indication and remains on the login view.

### 5.3 Authenticated Request

1. Dashboard attaches the stored JWT as a Bearer token to a protected API request.
2. ASP.NET Core authentication/authorization middleware validates the JWT signature and expiry.
3. The Backend checks the JWT's `jti` against the `AdminSession` record; the request proceeds only if the session exists and is not revoked.
4. On success, the request reaches the target controller/service.
5. On failure (invalid signature, expired token, or revoked/absent session), the request is rejected before reaching business logic.

### 5.4 Logout / Session Revocation

1. Admin triggers logout from the Dashboard.
2. Dashboard calls `POST /api/v1/auth/logout` with the current JWT attached.
3. Backend marks the corresponding `AdminSession` record as revoked and returns a success response. This is the first logout for that session.
4. Dashboard discards its locally stored JWT and returns to the login view. Client-side token deletion occurs regardless of whether the logout response succeeds, so the browser always returns to a logged-out state.
5. Any subsequent request presenting the same JWT (whether from the original client or a copy made before logout) is rejected at the session-validation step in §5.3, because the `AdminSession` record is revoked — client-side token discard alone does not invalidate the token.
6. A second logout attempt using the same, already-revoked JWT is rejected as unauthorized (401): the Backend does not treat an already-revoked token as a valid authenticated request, does not create a new session, and does not reactivate the revoked record.

### 5.5 Expired or Revoked JWT on Protected Access

1. A request is made to a protected Dashboard route or Backend API endpoint with a JWT that is either expired or corresponds to a revoked `AdminSession`.
2. The Backend rejects the request (expired signature/expiry check, or revoked-session check) before business logic executes.
3. The Dashboard, upon receiving the authentication-failure response, clears any locally held session state and redirects the Admin to the login view.

## 6. Backend Responsibilities

- Store the Admin password only as a salted hash produced by an adaptive hashing facility; never store or log plaintext (ARCH-001 §15.2).
- Issue JWTs containing a unique `jti` and expiry upon successful login.
- Maintain an `AdminSession` record per issued session, tracking issuance and revocation state (ARCH-001 §13.1, §15.3, ADR-013).
- Validate, on every protected request, both the JWT (signature, expiry) and the corresponding `AdminSession` (existence, non-revoked state).
- Reject requests failing either check before any controller/business logic executes.
- Mark the `AdminSession` revoked on logout.
- Never write passwords, JWTs, or session identifiers to plaintext application logs.
- Apply Admin JWT authentication/authorization middleware uniformly to all Dashboard-initiated Backend API operations except `POST /api/v1/auth/login`, consistent with FR-AUT-002. Jetson Agent activation (`POST /api/v1/activate`) is likewise exempt from Admin JWT authentication under the SRS, but its own authentication mechanism (Activation Key, and subsequently Device ID/shared secret) is out of scope for this feature and is specified separately.
- Accept `credentialIdentifier` and `password` as the two login request fields; the concrete meaning of `credentialIdentifier` (e.g., username or email) is an Implementation Plan detail, not a requirement of this feature.

## 7. Angular Dashboard Responsibilities

- Present a login view collecting `credentialIdentifier` and `password` and submitting them to `POST /api/v1/auth/login`.
- Store the issued JWT for the duration of the browser session and attach it as a Bearer token on every subsequent protected API request (Auth module, ARCH-001 §10.5).
- Guard protected routes so that navigation to any protected view without a valid stored session redirects to login rather than rendering the view.
- Provide a logout action that calls `POST /api/v1/auth/logout` and then discards the locally stored JWT and any cached protected-view state, regardless of whether the logout response succeeds.
- Treat any authentication-failure response (e.g., unauthorized/expired/revoked) from a protected API call as equivalent to a logged-out state: clear local session state and redirect to login.
- Contain no authentication business logic beyond credential submission, token storage/attachment, and route guarding (NFR-MNT-001).
- **Route guards are a user-experience control only.** They prevent navigation to protected views when no local session is available, but they are not the authoritative security boundary. The Backend must independently authenticate and authorize every protected API request; the Dashboard's route guard does not, by itself, protect Backend data.

## 8. Data Requirements

| Entity/Field | Purpose |
|--------------|---------|
| `AdminUser.UserId` | Identifies the single Admin account. |
| `AdminUser.PasswordHash` | Salted adaptive hash of the Admin password; never a recoverable/plaintext value (ARCH-001 §13.1, §15.2). |
| `AdminSession.SessionId` (`jti`) | Unique session identifier embedded in the issued JWT; used to correlate a bearer token to a revocable server-side record. |
| `AdminSession.UserId` | Associates the session with the Admin account. |
| `AdminSession.IssuedAt` | Session issuance timestamp. |
| `AdminSession.ExpiresAt` | Session/token expiry, mirrored in the JWT's own expiry claim. |
| `AdminSession.Revoked` | Boolean revocation flag; set on logout; checked on every protected request. |

No other authentication-related entities are introduced by this feature (ARCH-001 §13.1).

## 9. API Contract (Feature-Specification Level)

### 9.1 `POST /api/v1/auth/login`

| Aspect | Detail |
|--------|--------|
| Auth required | None (exempt per FR-AUT-002) |
| Request fields | `credentialIdentifier`, `password` |
| Success response | Standard envelope (`{success, message, data}`); `data` contains the issued JWT |
| Success status code | 200 |
| Failure (invalid credentials) | Standard error envelope (`{success, message, errorCode}`); no JWT issued |
| Failure status code | 401 |

### 9.2 `POST /api/v1/auth/logout`

| Aspect | Detail |
|--------|--------|
| Auth required | Valid JWT + active `AdminSession` |
| Request fields | None beyond the Bearer token |
| Success response | Standard envelope; `AdminSession` marked revoked |
| Success status code | 200 |
| Failure (no/invalid/already-revoked session) | Standard error envelope |
| Failure status code | 401 |

### 9.3 Representative Protected Endpoint Behavior

Any protected endpoint (e.g., `GET /api/v1/branches`, per ARCH-001 §14.1) exhibits the following behavior with respect to this feature:

| Condition | Status Code | Response |
|-----------|--------------|----------|
| Valid JWT, active non-revoked session | 200 (or endpoint-specific success code) | Standard envelope with requested data |
| Missing Authorization header | 401 | Standard error envelope |
| Malformed or invalid-signature JWT | 401 | Standard error envelope |
| Expired JWT | 401 | Standard error envelope |
| Valid JWT but revoked/absent `AdminSession` | 401 | Standard error envelope |

All responses use the uniform response envelope defined in ARCH-001 §14.3 / ADR-009; no binary or signaling exception applies to authentication endpoints.

## 10. Security Rules

- Passwords are stored exclusively as salted hashes produced by an adaptive hashing algorithm; plaintext passwords are never persisted or logged (ARCH-001 §15.2).
- Every protected request must pass both JWT signature/expiry validation and `AdminSession` non-revoked validation; neither check alone is sufficient (ARCH-001 §15.3).
- Discarding a JWT client-side does not invalidate it; only a Backend-side `AdminSession` revocation makes a token unusable. A copied token issued before logout is rejected after logout solely because of this server-side check.
- No refresh-token flow exists; a new login is required once a session expires or is revoked.
- JWTs and session identifiers are never written to plaintext application logs (ARCH-001 §15.6).
- No OAuth/OIDC, mTLS, client certificates, or HTTPS-specific mechanism is introduced by this feature (ARCH-001 §15.6, §28.2 — deferred to future hardening).
- Dashboard route guards are a user-experience control only; they are never treated as a substitute for Backend-side authentication and authorization of every protected API request.

## 11. Validation Rules

- Login requests must include both `credentialIdentifier` and `password`; a request missing either is rejected without attempting hash verification.
- A JWT presented on a protected request must be well-formed and carry a `jti` claim resolvable to an `AdminSession` record.
- A JWT missing the `jti` claim is rejected (401) without a session lookup.
- A JWT whose `jti` has no matching `AdminSession` record is rejected (401).
- An `AdminSession` that exists but whose associated `UserId` does not match the user the JWT was issued for is rejected (401) as a mismatched user/session association.
- An `AdminSession` lookup that finds no matching, non-revoked, non-expired record is treated identically to an invalid token for response purposes (401), without revealing which specific check failed.

## 12. Error Cases

| Case | Handling |
|------|----------|
| Invalid credentials at login | 401; no JWT issued; no `AdminSession` created |
| Missing Authorization header on protected request | 401; request rejected before business logic |
| Expired JWT on protected request | 401; request rejected before business logic |
| Revoked session with otherwise-valid JWT | 401; request rejected before business logic |
| Logout called without a valid session | 401; standard error envelope |
| Second logout attempt using an already-revoked JWT | 401; no new session created; revoked record not reactivated |
| JWT missing the `jti` claim | 401; rejected before any session lookup |
| JWT `jti` with no matching `AdminSession` | 401; request rejected before business logic |
| Valid session but mismatched user/session association | 401; request rejected before business logic |
| Direct browser navigation to a protected Dashboard route without a valid stored session | Dashboard redirects to login; no protected view rendered |
| Dashboard route guard bypassed, followed by a direct API request without a valid JWT | Backend independently rejects the API request (401); the route guard's bypass has no effect on Backend enforcement |

## 13. Acceptance Criteria

| # | Acceptance Criterion | Traces To |
|---|------------------------|-----------|
| AC-1 | Submitting valid Admin credentials to login produces a usable session (JWT issued, corresponding `AdminSession` created). | FR-AUT-001 |
| AC-2 | Submitting invalid credentials to login is rejected and produces no usable session. | FR-AUT-001 |
| AC-3 | No protected Dashboard view or protected Backend API endpoint is accessible without a valid, non-revoked session. | FR-AUT-002 |
| AC-4 | `POST /api/v1/auth/login` is accessible without an existing Admin session. (Jetson Agent activation is separately exempt from Admin JWT authentication under the SRS, but its behavior is governed by a later Feature Specification, not FS-01.) | FR-AUT-002 |
| AC-5 | Triggering logout invalidates the current session such that it cannot be used for any further protected request. | FR-AUT-003 |
| AC-6 | A token copied before logout is also rejected after logout, because invalidation is enforced server-side via `AdminSession`, not by client-side token deletion. | FR-AUT-003 |
| AC-7 | All protected Dashboard views and Dashboard-initiated Backend API operations other than login require an authenticated, non-revoked session. | NFR-SEC-001 |

## 14. Test Scenarios

### Positive

- T-01: Valid credentials → login succeeds → JWT issued → protected endpoint accessible with the JWT.

### Negative

- T-02: Invalid credentials → login rejected (401) → no JWT issued.
- T-03: Protected endpoint called with no Authorization header → rejected (401).
- T-04: Protected endpoint called with a malformed/invalid-signature JWT → rejected (401).

### Session Expiry

- T-05: Protected endpoint called with a JWT past its expiry → rejected (401), independent of revocation state.

### Logout Invalidation

- T-06: Log out → immediately retry the same protected request with the same JWT → rejected (401).
- T-10: Log out with a valid active session → session is marked revoked and the logout call returns success.
- T-11: Reuse the same token after logout on any protected request → rejected (401).
- T-12: Attempt a second logout using the already-revoked token → rejected (401); no new session created; revoked record not reactivated.

### Copied Token After Logout

- T-07: Capture a valid JWT before logout → log out → present the captured JWT on a protected request → rejected (401), because the associated `AdminSession` is revoked server-side.

### Protected Route/API Access Without a Token

- T-08: Navigate directly to a protected Dashboard route with no stored session → Dashboard redirects to login without rendering protected content.
- T-09: Call a protected Backend API endpoint with no token at all → rejected (401).
- T-13: Bypass the Dashboard route guard (e.g., direct API call) without a valid JWT → Backend independently rejects the request (401), demonstrating the route guard is not the security boundary.

### Malformed/Mismatched Session Claims

- T-14: Protected request with a JWT missing the `jti` claim → rejected (401).
- T-15: Protected request with a JWT `jti` that has no matching `AdminSession` → rejected (401).
- T-16: Protected request with a JWT whose session exists but is associated with a different `UserId` → rejected (401).

## 15. Out of Scope

Consistent with SRS BR-001, ASM-005, and ARCH-001 §2.5/§28.2, this feature explicitly excludes:

- User registration or account creation flows.
- Password reset or recovery.
- Multiple user accounts, roles, or permission levels.
- Refresh-token issuance or rotation.
- OAuth/OIDC or any third-party/social login.
- Multi-factor authentication (MFA).
- Email verification.
- HTTPS/TLS implementation (transport security is a deployment/future-hardening concern per ARCH-001 §15.6, §28.2, not part of this feature's behavior).
- "Remember me" or persistent-login functionality beyond the issued session's own expiry.

## 16. Traceability Matrix

| Requirement/Decision | Realized In This Feature |
|------------------------|----------------------------|
| FR-AUT-001 | §5.1, §5.2, §9.1, AC-1, AC-2 |
| FR-AUT-002 | §5.3, §5.5, §6, §9.3, AC-3, AC-4 |
| FR-AUT-003 | §5.4, §9.2, AC-5, AC-6 |
| NFR-SEC-001 | §5.3, §5.5, §7, §10, §11, AC-7 |
| BR-001 | §2, §3 (single Admin account, no role separation) |
| ARCH-001 §15.2 | §6, §10 (password hashing) |
| ARCH-001 §15.3, ADR-013 | §5.3, §5.4, §8, §10 (session revocation model) |
| ADR-002 (amended) | §5.1, §5.3 (JWT Bearer mechanism) |
| ADR-009 | §9 (API conventions, response envelope) |

## 17. Open Implementation Details (Deferred to Implementation Plan)

The following are implementation parameters, not requirements, and are intentionally left open for the Implementation Plan phase:

- Exact JWT lifetime/expiry duration.
- Exact adaptive password-hashing algorithm and its configuration parameters (e.g., work factor).
- Exact credential-identifier field name/format (e.g., "username" vs. "email").
- Exact `AdminSession` cleanup/pruning strategy for expired-but-unrevoked records.
- Specific HTTP header/claim naming conventions beyond the standard JWT Bearer scheme.
