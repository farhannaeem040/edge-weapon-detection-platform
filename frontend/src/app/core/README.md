# `core`

Cross-cutting, application-wide singletons (IP-01 §3, §5).

Contents:

- `auth.interceptor.ts` — attaches the JWT Bearer header to Backend API calls (T-24). It skips the
  endpoints that authenticate no Admin session (`auth/login`, `activate`) and never attaches the
  token to a non-Backend origin.
- `session-expiry.interceptor.ts` — treats a 401 from a protected Backend call as a logged-out
  state: clears local state and returns to `/login` (FS-01 §5.5, T-25). Only 401 does this, and
  only for session-bearing Backend requests.
- `auth.guard.ts` — route guard; **explicitly not a security boundary** (FS-01 §10, T-24).

The guard is a user-experience control. It reads browser storage and nothing more, so a copied,
expired, forged, or revoked token still satisfies it. Protected data is safe because the Backend
independently validates the JWT and the non-revoked `AdminSession` on every request — never
because of the guard.
