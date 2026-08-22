# Analytics (FS-15 / IP-17)

The Admin-only **Operational Analytics** page at `/analytics`, inside the existing authenticated
shell. It is a read-only view over the Alerts the platform has already persisted — it creates,
mutates and deletes nothing.

| File | Role |
|---|---|
| `analytics.routes.ts` | `ANALYTICS_ROUTE` and the three filter query-param names, defined once so the shell nav and `app.routes.ts` cannot drift |
| `analytics.models.ts` | The wire contract, transcribed field-for-field from the Backend's `OperationalAnalyticsDto`, plus the filter-state type and the option lists |
| `analytics.service.ts` | One `getOperational(filter)` read + `downloadCsv(filter)`; envelope unwrap; 404 → `null` |
| `operational-analytics.ts` | The page: Stitch-derived layout, URL-backed shared filter state, per-card loading/empty/error states, CSV export, print report |
| `../shared/charts/*` | The three dependency-free inline-SVG/CSS chart components and their shared geometry helpers |

## What each number means

Full definitions live in `specs/features/FS-15-operational-analytics-dashboard.md` §5. In short:

- **Detections over time** — a count of `Alert` rows bucketed by `DetectedAtUtc` (UTC). One Alert is
  one detection event that the Agent delivered and the Backend accepted. Quota-suppressed detections
  are **not** included; they are reported separately in the summary.
- **Detection confidence** — the mean of `Alert.Confidence` plus its high/medium/low band split. This
  is the detector's own score. It is **not** accuracy, precision, or a false-positive rate, and the
  card says so: `AlertStatus` has a single member (`New`), so the database holds no human-validated
  ground truth. The Stitch mockup's "98.4 % precision" donut is therefore not implementable and was
  replaced rather than faked.
- **Alert density by Branch** — Alerts per Branch, attributed through `Alert.CameraId →
  Camera.BranchId`. Branch names come from the database. A Branch with no Alerts appears at zero.
- **Alert delivery latency** — `ReceivedAtUtc − DetectedAtUtc`. It is **not** operator response time;
  no acknowledgement or resolution timestamp exists anywhere in the schema. Samples are restricted to
  `0 ≤ latency ≤ 1 day` to exclude clock skew and store-and-forward backlog replays, and the number
  of exclusions is always displayed.

## Rules this module holds to

1. **No fabricated data.** Every figure is read from the database or derived by a documented formula.
   Nothing is seeded, and no value, name, or percentage from the Stitch mockup appears anywhere.
2. **Unavailable is not zero.** A missing metric renders `—`, and an unmeasured chart bucket renders
   as a gap. `0 %` and `0 ms` are reserved for measured zeros.
3. **One filter state, one request.** All four cards are driven by the same window; there is no
   per-card fetch, so the cards cannot describe four slightly different moments.
4. **Branches are identified by GUID on the wire**, never by display name.
5. **No charting library.** The project ships no UI framework; four charts do not justify reversing
   that (IP-17 §1.5). The charts are plain SVG and CSS.
6. **No dead controls.** *Export CSV* downloads a Backend-generated file for the current filters;
   *Generate report* prints the same real metrics through the browser's Print → Save as PDF.

## Design source

The layout is adapted from the Stitch project `12701037052481013848`, screen
`03a107b0f10b4058adaf3ffcc4a2e3f7` ("Operational Analytics") — the header/action composition, the
filter card, the 12-column 8/4 + 6/6 bento grid, the card proportions, and the charcoal status
footer. The Stitch *branding* is not adopted: the LJMU shell, sidebar and header are untouched, and
the mockup's product name, persona, avatar, search field, notification bell and extra sidebar entries
are all deliberately absent. The full element-by-element mapping is recorded in IP-17 §2.
