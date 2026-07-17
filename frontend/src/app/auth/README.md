# `auth`

Authentication feature (IP-01 §3; FS-01).

Contents:

- `login.ts` / `login.html` / `login.css` — `LoginComponent`, the Admin login view (T-23). It
  collects only the two fields the Backend contract defines (`credentialIdentifier`, `password`)
  and shows one generic message for every failure, so it never reveals which was wrong.
- `auth.service.ts` — `AuthService`: login/logout calls and token storage (T-23).
- `auth.routes.ts` — the login and protected-landing route paths, defined once.

The issued JWT is held in `sessionStorage` under a single key constant: FS-01 §7 scopes it to the
browser session, and FS-01 §10 excludes any refresh-token flow, so it must not outlive the tab. It
is never logged, never rendered, and never returned to components. The JWT is not decoded — no
client-side claim is treated as authoritative. The Backend's signature/expiry and `AdminSession`
validation remains the security boundary (FS-01 §5.3, §10).
