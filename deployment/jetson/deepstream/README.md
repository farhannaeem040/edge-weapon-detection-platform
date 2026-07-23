# DeepStream Runtime Integration — deployment (IP-06, FS-04 Phase 1)

Generic, profile-based DeepStream supervision for the Jetson Agent. See
`specs/features/FS-04-deepstream-runtime-integration.md` and
`specs/implementation-plans/IP-06-deepstream-runtime-integration.md` for the full design.

**Nothing here is YOLOv4-specific by architecture.** `yolov4-fp16` is the first validated model
*profile* — a self-contained set of model assets. A future model is a new profile directory, never a
code change.

**Disabled by default.** `WDA_DEEPSTREAM_ENABLED` defaults to `false` — even with a profile fully
deployed (below), the Agent will not launch DeepStream until this is explicitly set to `true` in
`/etc/weapon-detection-agent/agent.env` and the Agent is restarted. This is a deliberate rollout gate
separate from credential/Operational state gating.

## Contents

| Path | Purpose |
|------|---------|
| `deepstream-app.txt` | The single, profile-agnostic DeepStream application config the Agent always launches. Its `[primary-gie] config-file=` line is the one line that names the active profile. |
| `deploy-engine.sh` | Installs a validated `.engine` file as a named profile's `model.engine`, after verifying it against that profile's manifest. |
| `deploy-sample-video.sh` | Stages a local test video (for local-video lifecycle verification, T-77) as `samples/deepstream/input.mp4`. Never committed — see `.gitignore`. |
| `verify-deepstream.sh` | Opt-in verification (static checks always; `--run` also does a real bounded launch). |
| `profiles/<profile>/infer-config.txt` | The profile's `[property]` inference config — every model-specific value (dimensions, precision, parser, class count) lives here, never in Agent code. |
| `profiles/<profile>/labels.txt` | The profile's class labels. |
| `profiles/<profile>/manifest.env` | The profile's manifest — checksum and every fact needed to verify the engine before it is trusted (FS-04 §8.3). |

Installed by `install.sh` into `/opt/weapon-detection/config/deepstream/` (config/labels/manifests
only). `models/<profile>/model.engine` is installed **only** by `deploy-engine.sh`, and the local
test video at `samples/deepstream/input.mp4` **only** by `deploy-sample-video.sh` — both run
manually, never automatically by `install.sh`.

## Staging the local test video

```bash
sudo /opt/weapon-detection/agent/deployment/jetson/deepstream/deploy-sample-video.sh \
    --source /path/to/staged/test_video.mp4
```

Refuses a non-regular-file source (symlink/directory/device), refuses to overwrite an existing
`input.mp4` without `--force`, installs atomically, sets `0640 weapon-detection:weapon-detection`,
and never prints the video's contents.

## Deploying a model profile's engine

The engine itself (`*.engine`) is never committed — it is a large, device/runtime-specific build
artifact. Stage it on the Jetson (e.g. `scp` it alongside its manifest), then:

```bash
sudo /opt/weapon-detection/agent/deployment/jetson/deepstream/deploy-engine.sh \
    --profile yolov4-fp16 \
    --engine /path/to/staged/yolov4_fp16.engine \
    --manifest /opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/manifest.env
```

This refuses to install unless: the profile name is safe, the engine is a regular `.engine` file (no
symlink/directory), the manifest is complete, the engine's SHA-256 matches
`manifest.env`'s `ENGINE_SHA256`, the profile's `infer-config.txt`/labels file are already present,
and any referenced parser library exists on this device. It never trusts the source filename. It
refuses to overwrite an existing `model.engine` for that profile without `--force`. Nothing is
printed except filenames, sizes, and hashes — never the engine's binary content.

**Adding a new profile:** create `deployment/jetson/deepstream/profiles/<new-name>/` with its own
`infer-config.txt`, `labels.txt`, and `manifest.env` (commit these — they are small text/metadata),
run `install.sh` (or `update.sh`) to sync them to the device, then `deploy-engine.sh --profile
<new-name> ...` to install its engine. Nothing in `DeepStreamProcessManager` or `AgentSettings`
changes.

**Switching the active profile:** edit the one `config-file=` line in
`/opt/weapon-detection/config/deepstream/deepstream-app.txt` to point at the new profile's
`infer-config.txt`, then restart the Agent (`sudo systemctl restart weapon-detection-agent`). This is
a deliberate, manual operator action — the Agent never decides this for itself.

## Local-video validation procedure (Phase 1)

1. Stage a test video with `deploy-sample-video.sh` (installs it at
   `/opt/weapon-detection/samples/deepstream/input.mp4`, the path `deepstream-app.txt`'s
   `[source0] uri=` references — never commit the video itself).
2. Run static checks: `sudo .../deepstream/verify-deepstream.sh`.
3. Run a real bounded launch: `sudo .../deepstream/verify-deepstream.sh --run --timeout=60`.
4. Confirm the Agent itself supervises it correctly: start the real Agent (`systemctl start
   weapon-detection-agent`) with an activated identity, confirm exactly one `deepstream-app` process
   via `pgrep -f deepstream-app`, then `systemctl stop weapon-detection-agent` and confirm it exits
   cleanly (no zombie, no orphan — `pgrep -f deepstream-app` returns nothing).

## Rollback procedure

If DeepStream supervision causes any Agent instability:

```bash
sudo systemctl stop weapon-detection-agent
```

Then choose one:

- **Fastest, no redeploy:** rename `/opt/weapon-detection/config/deepstream/deepstream-app.txt` out
  of the way (or simply never run `deploy-engine.sh`, leaving no `model.engine` installed) —
  `DeepStreamProcessManager.start()` will fail immediately (the executable/config still resolve, but
  DeepStream itself will fail to load a missing engine), which the coordinator's existing rollback
  handles safely (T-60) without touching Device Identity.
- **Full revert:** redeploy the pre-IP-06 Agent code via `update.sh` from the prior commit, or edit
  `agent/src/weapon_detection_agent/main.py` to call `create_app()` with no `components_factory`
  override (reverting to the always-safe empty-components default) and re-run `install.sh`.

In every case, rollback never risks Device Identity, `ActivatedAt`, or the SQLite store —
`DeepStreamProcessManager` is wired in only through `main.py`'s explicit override and touches no
activation/identity/database code path.
