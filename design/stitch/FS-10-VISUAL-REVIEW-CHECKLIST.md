# FS-10/IP-12 — Manual Browser Design-Validation Checklist (Round 3, post-corrections)

Isolated environment is live with the three Round-2 corrections applied. This round needs you to check
the three corrected areas specifically — Rounds 1–2's other checks (login, shell, responsive,
accessibility, console/network) are unaffected and don't need repeating unless you want to.

## Access

- **Frontend (open this):** http://localhost:4400
- **Admin login:** `visual-admin` / `Visual_Review_Passw0rd!2026`
- Fully isolated from production — production containers were never started, stopped, or touched.

## Seeded dataset (two independent Branches, both live now)

| | Branch A — "Riverside Retail Park - North Entrance Security Office" | Branch B — "Harbourside Distribution Centre" |
|---|---|---|
| Quota today | **15 / 15 — exhausted** | 5 / 15 — well under |
| Suppressed | 12 (6 gun / 6 knife) | 0 |
| Camera | Main Entrance Camera — `rtsp://my-server-ip:8554/camera1` | Loading Bay Camera — `rtsp://my-server-ip:8554/camera2` |
| Device/Camera count | 1 / 1 | 1 / 1 |

Exhausting Branch A did **not** touch Branch B's counters — confirmed both via the API (below) and via
two new real-SQL-Server integration tests.

## What changed since Round 2 — check these three things specifically

### Correction 1 — Camera stream URL (the processed-stream card is gone)

Open `/branches/ad40cd56-51a3-44b1-a853-66b191806de8` (Riverside). Under **"Connected cameras"** each
camera now shows, in one place:

```
Camera name: Main Entrance Camera
Camera stream URL: rtsp://my-server-ip:8554/camera1
Status: Enabled
[Copy URL]
```

Check: the URL is **not** a clickable link, wraps safely, and the Copy button works. There is no
separate "Processed RTSP stream" card anymore — that concept was reverted. Branch B's camera shows
`rtsp://my-server-ip:8554/camera2`.

### Correction 2 — Alerts page Branch filter

Open `/alerts`. The filter bar now has a **Branch** dropdown (first field, before Date/Class/Snapshot),
populated from the real Branch list — "All Branches", "Riverside Retail Park...", "Harbourside
Distribution Centre". Check:

- Selecting a Branch and clicking **Apply filters** narrows the table and the "Page X of Y · N Alerts"
  count to just that Branch (15 for Riverside, 5 for Harbourside).
- The Branch filter combines correctly with the weapon-class filter.
- Refreshing the browser with a Branch selected keeps it selected (it's in the URL query string:
  `?branchId=...`).
- **Clear** resets the Branch filter back to "All Branches" along with everything else.
- Changing the Branch resets you to page 1.

### Correction 3 — Dashboard is explicitly Branch-scoped

Open `/dashboard`. There is now a **Branch:** dropdown next to the "Operations overview" heading,
populated from the real Branch list — never a silently-picked "first" Branch. Check:

- Selecting **Riverside** shows exactly its own quota card: "Riverside Retail Park ... daily Alert
  quota", "15 / 15 Alerts used today", "Shared across all devices and cameras in this Branch", and the
  red "Branch daily quota reached / Further detections for this Branch are being suppressed" banner.
- Selecting **Harbourside** shows exactly its own card: "5 / 15", 10 remaining, 0 suppressed — switching
  the dropdown should replace the whole card cleanly (no stale Riverside numbers flashing through).
- The selection is in the URL (`/dashboard?branchId=...`) and survives a refresh.
- A **"View Alerts for this Branch"** button/link at the bottom of the dashboard card carries the
  selected Branch into `/alerts?branchId=...` — the Alerts page should open already filtered to it.
- Try visiting `/dashboard` with no `branchId` at all: since two Branches now exist, you should see
  "Select a Branch above to view its dashboard." rather than the app silently guessing one for you.

## Backend-level proof (already verified, for your reference)

```
GET /api/v1/dashboard/summary?branchId=<Riverside>  -> today:15, remaining:0, suppressions.total:12
GET /api/v1/dashboard/summary?branchId=<Harbourside> -> today:5,  remaining:10, suppressions.total:0
GET /api/v1/alerts?branchId=<Riverside>&pageSize=1   -> totalCount:15
GET /api/v1/alerts?branchId=<Harbourside>&pageSize=1 -> totalCount:5
```

`GET /api/v1/dashboard/summary` with no `branchId` now returns `400 VALIDATION_ERROR` — there is no
"default Branch" anymore, by design.

## What this does NOT change

Login, shell nav, responsive breakpoints, accessibility behavior, polling cadence, snapshot placeholder
wording, error/offline states — none of those were touched in this pass.

## Reporting back

Same as before: note screen, viewport, what you saw vs. expected. I'll fix only what's actually wrong.
Nothing gets deployed or committed without your say-so. Tell me when you're done and I'll tear down the
isolated environment.
