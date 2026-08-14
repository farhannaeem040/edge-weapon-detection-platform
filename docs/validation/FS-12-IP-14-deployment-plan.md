# FS-12 / IP-14 — Production Migration and Deployment Plan (T-290)

| Field | Value |
|-------|-------|
| Task ID | T-290 |
| Realizes | FS-12, IP-14 |
| Date | 2026-08-02 |
| Status | **Not executed.** This document is the approved-in-advance plan; production remains unmigrated and undeployed. |

> **Nothing in this document has been run against production.** The migration is not applied, no image
> is deployed, no service restarted, no publisher started. Executing it requires separate explicit
> approval.

---

## 1. Pre-Deployment Backup and Baseline

| # | Step | Purpose |
|---|---|---|
| 1 | `BACKUP DATABASE WeaponDetection TO DISK = '<path>' WITH INIT, CHECKSUM` | Full recovery point |
| 2 | `RESTORE VERIFYONLY FROM DISK = '<path>' WITH CHECKSUM` | Proves the backup is restorable *before* changing anything |
| 3 | Copy `/opt/weapon-detection/database/agent.db` (Agent stopped or via `sqlite3 .backup`) | Jetson-side recovery point |
| 4 | `PRAGMA integrity_check;` on the copy | Proves the SQLite copy is sound |
| 5 | Record `DeviceIdentity` **operational state and DeviceId only** — never the protected secret; record `ConfigCache.configurationVersion` and `UpdatedAt` | Post-migration comparison without exposing credentials |
| 6 | Record every Camera's `CameraId`, `Name`, `SourceOrder`, `RtspUrl` | The GUIDs are the invariant the whole feature must preserve |
| 7 | Record Agent and Bridge PIDs and `systemctl show -p NRestarts` | Detects an unintended restart |
| 8 | Record both current output URLs (`cameras/{GUID}` form) | The "old" side of the URL transition |
| 9 | Confirm zero FFmpeg publishers on the Windows host | No detections during the window |
| 10 | Record `Alerts`, `BranchDailyAlertQuotas.AcceptedAlertCount`, and Jetson pending/delivered counts | Proves nothing is lost |

Current baseline at time of writing: **Alerts = 4, quota accepted = 4, pending = 0, `CameraKey` column absent.**

---

## 2. Expected Migration Effect

**Device** (single production Device):

| Column | Before | After |
|---|---|---|
| `JetsonHost` | *(column absent)* | `100.98.226.80` |
| `RtspOutputPort` | *(column absent)* | `8554` |
| `AnnotatedOutputBaseUrl` | `rtsp://100.98.226.80:8554` | **unchanged — deliberately retained** |

**Cameras:**

| Camera | CameraId | CameraKey after |
|---|---|---|
| Front Camera | `2613b331-…` — **unchanged** | `front-camera` |
| Rear Entrance | `ad8a1f09-…` — **unchanged** | `rear-entrance` |

Any Camera not in the approved list backfills to its own GUID string, which keeps its mount
byte-identical and therefore produces no `configurationVersion` change for it.

**Untouched:** every `Alert`, every `DetectionEvent`, `BranchDailyAlertQuotas`, `DeviceId`,
`ProtectedSharedSecret`, `ActivationStatus`, all Activation Keys, all snapshot settings.

### URL transition

```
Old:  rtsp://100.98.226.80:8554/cameras/2613b331-8783-4d51-903a-3e41a979a14c
      rtsp://100.98.226.80:8554/cameras/ad8a1f09-7fba-4794-8f73-63c7e2c57c92

New:  rtsp://100.98.226.80:8554/cameras/front-camera
      rtsp://100.98.226.80:8554/cameras/rear-entrance
```

The old GUID URLs **stop working**. Both formats are deliberately *not* served simultaneously — no
compatibility requirement was documented, and serving both would leave two public names for one
stream.

---

## 3. Runtime Sequence

1. Backup and baseline (§1).
2. Build images (already verified building cleanly in T-290).
3. Apply the migration **once**, via the existing `migrations` Compose service.
4. Recreate the `backend` container per the project's normal deployment procedure.
5. Confirm `GET /api/v1/health` returns 200 and `RestartCount` is as expected.
6. The Agent's coordinator fetches the FS-12 configuration on its next poll (≤30 s).
7. `configurationVersion` differs from the cached value — **once**.
8. The Agent validates the new configuration and persists it to `ConfigCache`.
9. The Agent performs **exactly one** controlled Bridge restart.
10. `cameras/front-camera` and `cameras/rear-entrance` mounts appear.
11. The old GUID mounts disappear with the old Bridge process.
12. `source_id` → GUID mapping is unchanged; `DetectionEvent.CameraId` stays the GUID.
13. No Alert or DetectionEvent is deleted.
14. Pending returns to zero.
15. The Dashboard displays the new output URLs.

**Downtime is not claimed to be zero.** The Bridge restart interrupts both annotated output streams
for the duration of pipeline teardown and rebuild, and a detection occurring in that window would not
be produced. The interruption has not been measured, so it must not be described as seamless.

---

## 4. Rollback

### 4.1 Before the migration is applied
Straightforward: redeploy the previous Backend/frontend images. No database state has changed.

### 4.2 After the migration, application only
The retained `AnnotatedOutputBaseUrl` column is what makes this viable — it is the specific reason
Option A was chosen over a direct migration. The previous Backend build reads that column, ignores
`CameraKey`/`JetsonHost`/`RtspOutputPort` entirely (they are additive and inert to it), and resumes
deriving `cameras/{GUID}` output paths.

Consequences to expect, not to be surprised by:

* `configurationVersion` changes **back**, so the Agent performs one *further* controlled Bridge
  restart onto the GUID mounts.
* The Agent's cache will hold CameraKey paths at that moment. The FS-12 Agent reads a legacy
  GUID-path cache; an **older** Agent reading a CameraKey cache is the untested direction. Rolling the
  Agent back therefore means rolling its `ConfigCache` back too, or letting it re-fetch from the
  rolled-back Backend before starting the Bridge.
* Public URLs revert to the GUID form.

### 4.3 Database rollback
The migration's `Down` drops `CameraKey`, `JetsonHost` and `RtspOutputPort` and the unique index. It is
correct and tested, **but it is not the recommended production rollback**: it discards the Device's
network configuration, which would then have to be re-entered by an administrator.

**Preferred emergency path:** restore the verified SQL backup from §1 together with the previous
application image, per the project's existing recovery policy. `Down` is appropriate for a disposable
or pre-production database, not for the one holding the Alert history.

### 4.4 Runtime rollback (Jetson)
Restore the previous Agent/Bridge images, restore or clear the `ConfigCache` so the Agent fetches a
compatible configuration, verify the GUID mounts return, and confirm `DeviceIdentity` and the
protected secret are untouched. **Do not regenerate the Activation Key** — a rollback is not a
credential event, and regenerating would force a reactivation that the rollback did not require.

---

## 5. Safest Realistic Path

Given a single Branch, a single Device and two Cameras, the lowest-risk sequence is:

1. Verified SQL backup + verified SQLite copy.
2. Apply the migration in a maintenance window with publishers stopped, so no detection can be in
   flight across the one Bridge restart.
3. Verify the two new mounts and the unchanged `CameraId` values before resuming any publisher.
4. Keep `AnnotatedOutputBaseUrl` in place until a later feature retires it, so §4.2 stays available.

If anything fails between steps 2 and 3, the correct response is §4.3 (restore the backup), not `Down`.
