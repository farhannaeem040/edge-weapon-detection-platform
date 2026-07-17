# Frontend — Angular Dashboard

The Admin Dashboard for the Edge-Based Weapon Detection platform (IP-01 tasks T-22 onward; FS-01,
FS-02 Increment A). Generated with Angular CLI 20.3.32.

The root [`README.md`](../README.md) is the source of truth for project status, prerequisites,
architecture, the API contract, security notes, and known limitations. This file covers only how to
work in this workspace.

## Module boundaries

| Folder | Responsibility |
|--------|----------------|
| [`src/app/core`](src/app/core/README.md) | Cross-cutting singletons: auth interceptor, session-expiry interceptor, route guard |
| [`src/app/auth`](src/app/auth/README.md) | `AuthService`, login view |
| [`src/app/branches`](src/app/branches/README.md) | Branch list/detail/create, cameras, Activation Key display, device status badge |
| [`src/app/shared`](src/app/shared/README.md) | The protected dashboard shell |

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

## Backend base URL

Configured per build environment in `src/environments/`:

- `environment.development.ts` — used by `ng serve` and development builds; host-relative `/api/v1`,
  which `proxy.conf.json` forwards to the local Kestrel HTTP endpoint.
- `environment.ts` — the production default; host-relative `/api/v1`, since the Dashboard's static
  assets are served from the same host as the Backend in the prototype deployment.

These files hold **only** the non-secret base URL. No credential, token, or key belongs in an
environment file — they are compiled into the browser bundle and readable by anyone who loads the
page. Angular selects the development file automatically via `fileReplacements` in `angular.json`.
