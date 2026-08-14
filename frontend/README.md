# Frontend

This is the web dashboard that security staff and admins use. It's called the **LJMU AI Security
Platform** in the UI.

## What it does

- **Sign in** — admin login, protected by a session token.
- **Branches** — create/edit/delete branches, manage their cameras, and issue the one-time
  activation key a Jetson device uses to join.
- **Live monitoring** — watch a branch's cameras live, with detections drawn on the video as they
  happen.
- **Alerts** — a searchable/filterable log of every weapon detection, with confidence score,
  camera, timestamp, and an evidence snapshot.
- **Dashboard** — an overview landing page.

## Tech

Angular (standalone components), talking to the Backend over `/api/...` on the same origin (no
separate API domain, so no CORS setup is needed).

## Where to look

| Folder | What's in it |
|---|---|
| `src/app/auth/` | Login page and session handling |
| `src/app/branches/` | Branch, camera, and device-activation screens |
| `src/app/monitoring/` | Live camera monitoring |
| `src/app/alerts/` | Alert list and alert detail |
| `src/app/dashboard/` | Landing page |
| `src/app/core/` | Shared plumbing: auth guard, HTTP interceptors |
| `src/app/shared/` | Shared UI: page shell/navigation, badges |

## Running it

```bash
npm ci
npm start
```

Opens at `http://localhost:4200` and expects the Backend to be running (see
[`../deployment/README.md`](../deployment/README.md)).

```bash
npm test -- --watch=false   # run tests
npm run build                # production build
```
