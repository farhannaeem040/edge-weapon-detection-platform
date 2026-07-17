# `shared`

Shared, reusable UI components and models (IP-01 §3).

Contents:

- `dashboard.ts` — `DashboardComponent`, the protected shell a logged-in Admin lands on (T-23/T-24),
  host of the logout action (T-25) and the navigation entry point to the branch views (T-26). It
  lives here because IP-01 §3 defines exactly four Angular modules (`core`, `auth`, `branches`,
  `shared`), and the shell is not a fifth. It stays deliberately thin.

Not implemented in this milestone, and deliberately so:

- `EnvelopeErrorComponent` / `ApiEnvelopeService` — IP-01 §3 anticipated a shared component and
  service for the standard `{ success, message, data }` envelope. Envelope unwrapping instead sits in
  each feature's own service (`AuthService`, `BranchService`), each of which raises a fixed, generic
  message on a contract violation rather than echoing Backend text into the UI. Extracting the shared
  abstraction is worth doing once a third consumer exists; with two, it would be indirection without
  enough callers to justify it.

`/dashboard` is currently the only route hosting the logout action, so signing out means navigating
there rather than reaching it from the branch views — see the root `README.md`'s known limitations.
