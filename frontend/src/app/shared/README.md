# `shared`

Shared, reusable UI components and models (IP-01 §3).

Contents:

- `shell.ts` — `ShellComponent`, the authenticated application shell introduced by the Stitch visual
  redesign. It provides the fixed charcoal sidebar (brand + navigation), the sticky top header, the
  Sign-out action, and the content container into which the branch views render. It is wired as a
  parent **layout route** in `app.routes.ts`, so every authenticated view shares one frame; all branch
  paths and the `authGuard` gate are unchanged (the guard moved to the parent route). **Navigation
  lists only Branches** — the sole implemented protected area. The Stitch sidebar's other entries
  (Live monitoring, Alerts, Incidents, Cameras, Edge devices, Analytics, System health, Users and
  access, Settings) back no implemented feature and are deliberately absent (no dead links). The
  sidebar collapses to an off-canvas panel below 900px via a keyboard-operable header toggle.
- `dashboard.ts` — `DashboardComponent`, the thin `/dashboard` landing an Admin can reach directly
  (T-23/T-24), host of a logout action (T-25) and a link to the branch views (T-26). It lives here
  because IP-01 §3 defines exactly four Angular modules (`core`, `auth`, `branches`, `shared`), and
  the shell is not a fifth. It is **not** part of the shell's navigation: login lands on `/branches`,
  and the route carries no meaningful content beyond what the shell already offers, so it is kept
  (route + tests preserved) but not surfaced as a nav item.

Not implemented in this milestone, and deliberately so:

- `EnvelopeErrorComponent` / `ApiEnvelopeService` — IP-01 §3 anticipated a shared component and
  service for the standard `{ success, message, data }` envelope. Envelope unwrapping instead sits in
  each feature's own service (`AuthService`, `BranchService`), each of which raises a fixed, generic
  message on a contract violation rather than echoing Backend text into the UI. Extracting the shared
  abstraction is worth doing once a third consumer exists; with two, it would be indirection without
  enough callers to justify it.

Since the Stitch redesign, the Sign-out action lives in the shell's top header and is reachable from
every authenticated view; `DashboardComponent` keeps its own logout action as well (it renders
outside the shell). The `EnvelopeError`/`ApiEnvelope` note above still holds.
