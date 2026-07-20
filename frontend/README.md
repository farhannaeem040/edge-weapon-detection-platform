# Frontend — Angular Dashboard

The Admin Dashboard for the Edge-Based Weapon Detection platform (IP-01 tasks T-22 onward; FS-01,
FS-02 Increment A; branch/camera edit and delete from FS-03 / IP-03 T-45–T-46). Generated with
Angular CLI 20.3.32.

The root [`README.md`](../README.md) is the source of truth for project status, prerequisites,
architecture, the API contract, security notes, and known limitations. This file covers only how to
work in this workspace.

## Module boundaries

| Folder | Responsibility |
|--------|----------------|
| [`src/app/core`](src/app/core/README.md) | Cross-cutting singletons: auth interceptor, session-expiry interceptor, route guard |
| [`src/app/auth`](src/app/auth/README.md) | `AuthService`, login view |
| [`src/app/branches`](src/app/branches/README.md) | Branch list/detail/create/edit, cameras, Edit/Delete actions and the delete-confirmation dialog, Activation Key display, device status badge |
| [`src/app/shared`](src/app/shared/README.md) | The authenticated `ShellComponent` (sidebar/header frame) and the `/dashboard` landing |

## Visual design (Stitch redesign)

The Dashboard is styled to the approved Stitch **"Sentinel Operational System"** visual language,
rebranded as **LJMU AI Security Platform**. The design is documented under
[`design/stitch/`](../design/stitch/) (design system, screen inventory, and the Angular
implementation map).

- **Design tokens** live as CSS custom properties in [`src/styles.css`](src/styles.css) (colours —
  Sentinel Green `#146B3A` and its supporting scale — surfaces, text, status/danger, typography,
  spacing, radii, shadows, layout/sidebar/header dimensions, focus ring, transitions), alongside a
  small set of shared primitive classes (`.btn`, `.card`, `.field`, `.badge`, `.banner`, `.icon-btn`,
  `.empty-state`, `.spinner`). Feature components consume these and add only their own layout.
- **No UI framework** (Tailwind/Bootstrap/Material) and **no font files** are introduced. Headings
  use Geist and body text Inter *only if the viewer already has them*; both degrade to the system
  sans-serif stack. Icons are inline SVG.
- **Responsive.** Desktop shows the fixed 260px sidebar + sticky header + fluid content (max 1440px).
  Below 900px the sidebar becomes an off-canvas panel toggled from the header; below ~820px the login
  drops its brand panel to a single centred form. Wide content scrolls within its own container.
- **Accessibility.** Every interactive control has a visible keyboard focus ring, semantic markup, and
  (for icon-only actions) an `aria-label` + `title` naming its target. The delete dialog is a labelled
  `role="dialog"` that moves focus to Cancel on open, supports Escape to cancel, blocks the background,
  and returns focus to its opener on close. Status is never conveyed by colour alone (badges carry
  text and a shape cue).

### Implemented vs deferred Stitch screens

Only screens backed by an existing feature are styled: **Sign-in**, the authenticated **shell**
(Operations Overview chrome only — no analytics widgets), **Branch Details**, and the **Branch
Configuration Form** (create/edit). Every other Stitch screen — Camera Management, Edge Devices, Live
Monitoring, Alerts Management, Alert Review, System Health, Operational Analytics, Settings — backs no
implemented feature and is **deferred**; it is neither built nor linked (no alerts, monitoring,
reports, health, analytics, device fleet, or detection settings exist). See
[`design/stitch/SCREEN-INVENTORY.md`](../design/stitch/SCREEN-INVENTORY.md).

Preserved unchanged by the redesign: all routes, authentication, `credentialIdentifier`/`password`
login, JWT interceptor, route guard, session-expiry handling, logout, branch listing/detail/create/
edit/delete, camera add/edit/remove and the minimum-one-camera rule, device activation status, the
Activation Key regeneration flow, the one-time (never persistent/masked) key display, all API request/
response contracts, and validation behaviour. No Backend or Agent source is touched.

## Commands

All commands run from `frontend/`. Angular CLI is a local dev dependency, so no global install is
needed — the `npm` scripts invoke it, and `npx ng ...` works for anything without a script.

```bash
npm ci                    # install exactly the locked dependency versions
npm start                 # ng serve — dev server at http://localhost:4200/
npm test -- --watch=false # single Karma + Jasmine run
npm run build             # production build into dist/ (git-ignored)
npm audit                 # dependency vulnerability check
```

`npm test` needs a Chrome/Chromium browser. In a headless environment, point Karma at one:

```bash
CHROME_BIN="/path/to/chrome" npx ng test --watch=false --browsers=ChromeHeadless
```

There is no `ng e2e` target: no end-to-end framework is configured, and the milestone's automated
coverage is this Karma unit/component suite plus the Backend integration suite.

## Running against the Backend

Start the Backend first (see the root README), then `npm start`. The dev server proxies `/api` to the
Backend, so the browser issues only same-origin requests — see `proxy.conf.json`. This mirrors the
co-located production deployment and avoids requiring a CORS policy the Backend does not otherwise
need.

### Containerized alternative

The whole platform (this dashboard, the Backend, SQL Server, and migrations) also runs as a Docker
Compose stack, with no local Node.js or .NET required:

```powershell
powershell -ExecutionPolicy Bypass -File deployment/start.ps1
```

There, `Dockerfile` builds the app with `npm ci` + `npm run build` and serves
`dist/frontend/browser` from Nginx, which reverse-proxies `/api/` to the Backend container — the same
single-origin arrangement `proxy.conf.json` provides in development, which is why **no environment
file changes** were needed for containerization. See [`deployment/README.md`](../deployment/README.md).

This does not affect `ng serve`, which continues to work exactly as described above.

## Backend base URL

Configured per build environment in `src/environments/`:

- `environment.development.ts` — used by `ng serve` and development builds; host-relative `/api/v1`,
  which `proxy.conf.json` forwards to the local Kestrel HTTP endpoint.
- `environment.ts` — the production default; host-relative `/api/v1`, since the Dashboard's static
  assets are served from the same host as the Backend in the prototype deployment.

These files hold **only** the non-secret base URL. No credential, token, or key belongs in an
environment file — they are compiled into the browser bundle and readable by anyone who loads the
page. Angular selects the development file automatically via `fileReplacements` in `angular.json`.
