# FS-08 / IP-10 — Snapshot Capture Root-Cause Analysis

| Field | Value |
|-------|-------|
| Date | 2026-08-02 |
| Status | Root cause identified from source; **no code changed yet** |
| Verdict | The snapshot branch taps the wrong point in the pipeline. The candidate-cache TTL is **not** the cause. |

---

## 1. Symptom

With `WDA_SNAPSHOT_CAPTURE_ENABLED=true` and the RTSP output branch enabled, real detections occur and
protocol-v2 detection persistence works, but **no JPEG ever reaches the Agent spool**.

## 2. Root cause — the snapshot tee is attached pre-demux and pre-OSD

`deployment/jetson/deepstream/bridge/app/deepstream_bridge/pipeline.py`:

```python
# line 173-176
per_camera_sources = [src for src in cfg.sources if src.output_path]
dynamic_outputs = cfg.rtsp_out.enabled and bool(per_camera_sources)

nvosd = None if dynamic_outputs else self._make("nvdsosd", "onscreendisplay")   # ← None in production
```

```python
# line 202-224
chain = [streammux, pgie, *([tracker] if ...), nvvidconv, *([nvosd] if nvosd is not None else [])]
tail = chain[-1]                       # ← in production this is nvvideoconvert, NOT an OSD

if cfg.snapshot.enabled:
    tee = self._make("tee", "osd-tee")
    tail.link(tee)
    self._attach_snapshot_branch(pipeline, tee, cfg.snapshot)   # ← taps the batched, un-annotated tail
    branch_source = tee

if dynamic_outputs:
    self._attach_per_camera_outputs(pipeline, branch_source, per_camera_sources, cfg.rtsp_out)
    #                               ^ nvstreamdemux + per-camera nvdsosd happen *after* this point
```

FS-11 moved the OSD **into each per-camera post-demux branch** and stopped building the shared
pre-demux `nvdsosd` entirely. The FS-08 snapshot branch was written against the older
single-shared-OSD topology and was never adapted. The element named `osd-tee` is, in production,
no longer attached to an OSD at all.

Consequences, in the order they bite:

1. **The tapped buffer is pre-OSD.** No bounding box, no class label, no confidence overlay — so even
   if a JPEG were produced it could not satisfy FS-08's "post-OSD annotated frame" contract.
2. **The tapped buffer is pre-demux and therefore batched** (`batch-size = 2`), carrying *both*
   cameras' surfaces in one `NvBufSurface`. A single-image `nvjpegenc` cannot meaningfully encode it,
   which is the most likely reason nothing is emitted downstream.
3. **There is exactly one snapshot branch, not N.** The required topology is one bounded snapshot
   branch per enabled Camera, downstream of that camera's own `nvdsosd`.

## 3. Secondary defect — correlation key is not source-safe

`snapshot.py` keys both structures by `frame_number` alone:

* `CandidateFrameTracker._candidate_frames: {frame_number: expiry}`
* `SnapshotCandidateCache._entries: {frame_number: (jpeg_bytes, expiry)}`

With N cameras demultiplexed into independent branches, frame numbers are per-source and **collide
across cameras**. Two simultaneous detections on `front-camera` and `rear-entrance` could resolve to
each other's JPEG — a cross-camera evidence error, which is the most serious failure mode this feature
could have. This must be fixed regardless of the topology change.

## 4. What the TTL actually is

`config.py`: `_DEFAULT_SNAPSHOT_FRAME_TTL_MS = 3000`, `_DEFAULT_SNAPSHOT_MAX_RETAINED_FRAMES = 32`.

The previously-suspected ~750 ms value is **not** what the current code uses; it is already 3 s. TTL
tuning cannot fix a branch that never receives an encodable single-camera buffer. Raising it further
would have produced no change and would have looked like a fix attempt that "didn't work" — which is
consistent with the reported history.

## 5. Required fix (not yet implemented)

1. **Move the snapshot branch into each per-camera branch**, after that branch's `nvdsosd`:
   `nvdsosd -> tee -> { existing encoder/RTSP branch , queue(leaky) -> valve -> caps/convert -> nvjpegenc -> appsink }`.
   The existing RTSP branch must stay byte-for-byte as it is today.
2. **Make correlation source-aware.** Key on the protocol `message_id` as primary identity, carrying
   `source_id` and `frame_number` as corroborating fields; never on `frame_number` alone.
3. **Two-sided rendezvous** so JPEG-before-ack and ack-before-JPEG both complete exactly once.
4. **Per-source cache isolation** with bounded entries and bounded total JPEG bytes.
5. Re-measure real detection→acknowledgement latency on hardware and set the TTL default from the
   measurement plus a safety margin, rather than by guess.

## 6. Scope conflict to resolve before the UI work

FS-08's header lists **"Angular UI"** under *Explicitly excluded*. The current task requires an Alerts
thumbnail. That is a deliberate scope extension of a frozen document, so FS-08 §Explicitly-excluded
must be amended (with rationale) rather than silently contradicted.
