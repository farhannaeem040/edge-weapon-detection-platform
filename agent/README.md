# Agent

This is the software that runs **on the Jetson device** at each branch.

## What it does

- Reads the camera streams for that branch.
- Runs the weapon-detection AI model (via NVIDIA DeepStream) on the video, frame by frame.
- When a weapon is detected, saves a snapshot as evidence and records the detection.
- Talks to the Backend: activates itself with a one-time key, downloads its camera configuration,
  and sends detection events and snapshots up to the central server.
- Keeps working locally (using a small SQLite database) even if the connection to the Backend is
  temporarily down, then syncs once it's back.

## Why it's separate from the Backend

The AI model needs to run physically close to the cameras, on hardware with a GPU (the Jetson),
so detection is instant and doesn't depend on a good internet connection. The Backend, in
contrast, is a normal server that can run anywhere.

## Tech

Python (FastAPI), built for JetPack / Ubuntu on the Jetson Orin Nano, using NVIDIA DeepStream for
the video/AI pipeline.

## Where to look

| Folder | What's in it |
|---|---|
| `src/weapon_detection_agent/activation/` | One-time device activation with the Backend |
| `src/weapon_detection_agent/configuration/` | Downloading and applying camera/device config |
| `src/weapon_detection_agent/deepstream/` | Building and running the DeepStream video pipeline |
| `src/weapon_detection_agent/detection/` | Handling detection events coming out of the pipeline |
| `src/weapon_detection_agent/snapshot/` | Capturing and uploading evidence snapshots |
| `src/weapon_detection_agent/sync/` | Sending detection events to the Backend |
| `src/weapon_detection_agent/persistence/` | Local SQLite storage |
| `src/weapon_detection_agent/runtime/` | Startup and overall process supervision |
| `tests/` | Automated tests |

## Running it

```bash
pip install -e .
python -m weapon_detection_agent.main
```

See `../deployment/jetson/README.md` for how it's actually installed and run on a real Jetson
device (as a systemd service).
