# Claude Project Instructions

Welcome to the Edge-Based Weapon Detection Platform.

You are acting as a software engineer working within an existing engineering team.

## Your Responsibilities

- Understand the project before making changes.
- Read relevant documentation before implementation.
- Follow the established architecture.
- Do not make architectural decisions without justification.
- Prefer maintainable and well-structured solutions.
- Explain important implementation decisions.

---

## Development Process

Every feature follows the same lifecycle:

Requirements

↓

Specification

↓

Implementation Plan

↓

Implementation

↓

Review

↓

Testing

↓

Documentation

---

## Before Implementing Any Feature

Always determine whether additional context is required.

Relevant documents may include:

- Project Charter
- Vision
- Software Requirements Specification
- Architecture
- Feature Specification

Only read the documents necessary for the current task.

---

## Coding Principles

- SOLID principles
- Clean Architecture where appropriate
- Small, focused commits
- Clear naming
- Dependency Injection
- Unit testing where practical

---

## Never Assume Requirements

If a requirement is unclear:

- Ask for clarification.
- Do not invent functionality.

---

## Repository Philosophy

Documentation is the source of truth.

Implementation must follow approved specifications.

Avoid modifying unrelated files.

---

## Windows RTSP Test Stream Control

These instructions are **authoritative** for publishing test video into the POC pipeline. They exist
because two real incidents were caused by ignoring them: a Scoop *shim* process was killed while the
real encoder kept streaming, and an unattended `-stream_loop -1` weapon publisher ran against
production.

### Environment

Windows test-video directory: `C:\stream`

Known files:

- `C:\stream\gun-tester.mp4`
- `C:\stream\gun-tester-hd.mp4`
- `C:\stream\handgun-cctv.mp4`
- `C:\stream\knife-tester.mp4`
- `C:\stream\knife-tester-1.mp4`
- `C:\stream\comands.txt`

MediaMTX input server: `rtsp://127.0.0.1:8554`

Production camera paths (publish here, from Windows):

- camera1 — `rtsp://127.0.0.1:8554/camera1`
- camera2 — `rtsp://127.0.0.1:8554/camera2`

The Jetson consumes the same streams over the Windows Tailscale address:

- camera1 — `rtsp://100.77.146.5:8554/camera1`
- camera2 — `rtsp://100.77.146.5:8554/camera2`

### Approved FFmpeg executable

Always resolve and use the real Scoop executable:

```
C:\Users\farha\scoop\apps\ffmpeg\current\bin\ffmpeg.exe
```

Do **not** execute `ffmpeg` by name. Do **not** use the Scoop shim, an Anaconda FFmpeg build, or any
unknown PATH-resolved FFmpeg.

Before launching, assert the executable path equals the approved path. Record only the path — never
sensitive environment contents.

### Reference continuous stream command

The user's known working reference command (manual continuous POC streaming):

```bat
"C:\Users\farha\scoop\apps\ffmpeg\current\bin\ffmpeg.exe" ^
  -hide_banner ^
  -re ^
  -stream_loop -1 ^
  -i "C:\stream\gun-tester.mp4" ^
  -map 0:v:0 ^
  -an ^
  -vf "scale=1280:720:flags=fast_bilinear,fps=30" ^
  -c:v h264_nvenc ^
  -preset p1 ^
  -tune ll ^
  -rc cbr ^
  -b:v 4000k ^
  -maxrate 4000k ^
  -bufsize 8000k ^
  -g 30 ^
  -bf 0 ^
  -pix_fmt yuv420p ^
  -f rtsp ^
  -rtsp_transport tcp ^
  rtsp://127.0.0.1:8554/camera1
```

Safety rules:

- `-stream_loop -1` must **never** be used automatically for a bounded detection test.
- Continuous looping requires explicit user approval.
- Genuine weapon tests default to non-looping input or a short explicit duration.

### Camera2 variant

To publish the same approved input to source1, change only the destination to
`rtsp://127.0.0.1:8554/camera2`.

Example bounded camera2 command:

```bat
"C:\Users\farha\scoop\apps\ffmpeg\current\bin\ffmpeg.exe" ^
  -hide_banner ^
  -re ^
  -i "C:\stream\handgun-cctv.mp4" ^
  -map 0:v:0 ^
  -an ^
  -t 15 ^
  -vf "scale=1280:720:flags=fast_bilinear,fps=30" ^
  -c:v h264_nvenc ^
  -preset p1 ^
  -tune ll ^
  -rc cbr ^
  -b:v 4000k ^
  -maxrate 4000k ^
  -bufsize 8000k ^
  -g 30 ^
  -bf 0 ^
  -pix_fmt yuv420p ^
  -f rtsp ^
  -rtsp_transport tcp ^
  rtsp://127.0.0.1:8554/camera2
```

### Publisher start requirements

When starting a stream through PowerShell:

1. Use `Start-Process` with `-PassThru`.
2. Use the actual executable path directly.
3. Store the returned real PID.
4. Assert the launched process `ExecutablePath` equals the approved FFmpeg executable.
5. Record: PID, source file, destination camera path, start time.
6. Never start a second publisher for the same RTSP path.
7. Check existing process command lines before launching.
8. Never rely only on the process name `ffmpeg`.

Conceptual pattern:

```powershell
$ffmpeg = 'C:\Users\farha\scoop\apps\ffmpeg\current\bin\ffmpeg.exe'

$args = @(
    '-hide_banner',
    '-re',
    '-i', 'C:\stream\handgun-cctv.mp4',
    '-map', '0:v:0',
    '-an',
    '-t', '15',
    '-vf', 'scale=1280:720:flags=fast_bilinear,fps=30',
    '-c:v', 'h264_nvenc',
    '-preset', 'p1',
    '-tune', 'll',
    '-rc', 'cbr',
    '-b:v', '4000k',
    '-maxrate', '4000k',
    '-bufsize', '8000k',
    '-g', '30',
    '-bf', '0',
    '-pix_fmt', 'yuv420p',
    '-f', 'rtsp',
    '-rtsp_transport', 'tcp',
    'rtsp://127.0.0.1:8554/camera2'
)

$process = Start-Process `
    -FilePath $ffmpeg `
    -ArgumentList $args `
    -PassThru `
    -WorkingDirectory 'C:\stream'
```

Do not include `-WindowStyle Hidden` during debugging unless the user explicitly requests it.

### Publisher stop requirements

Never stop only a Scoop shim or wrapper process — the shim spawns the real encoder as a **separate
child**, which keeps streaming after the shim dies.

Always terminate the complete process tree for the captured real PID:

```
taskkill /PID <real-pid> /T /F
```

After stopping, verify:

- the PID no longer exists;
- no child FFmpeg process remains;
- no matching command line remains;
- MediaMTX reports the camera path is no longer in use;
- Alert and DetectionEvent counts have stopped increasing.

Do not use broad commands such as `taskkill /IM ffmpeg.exe /F` unless an emergency has been
explicitly authorised — it can terminate unrelated FFmpeg jobs.

### Emergency targeted cleanup

When a PID was lost, identify processes using **both** the input filename and the destination path.

- camera1 — require both the approved input filename (e.g. `gun-tester.mp4`) **and**
  `rtsp://127.0.0.1:8554/camera1`.
- camera2 — require both the approved input filename **and** `rtsp://127.0.0.1:8554/camera2`.

Terminate only matching process trees.

### Test-stream policy

Non-weapon pipeline tests:

- prefer benign real-world video;
- synthetic colour bars / `testsrc` may produce model false positives;
- quota and detection counters must still be observed.

Genuine weapon tests:

- start the event watcher **before** starting FFmpeg;
- poll every 250–500 ms;
- use non-looping input or `-t 10` / `-t 15`;
- stop the full process tree after the first new DetectionEvent;
- enforce a maximum accepted-event safety ceiling;
- report all extra detections honestly;
- never delete events merely to make the test look cleaner.

### Preflight checks before every test

Confirm:

- Backend healthy;
- Agent Operational;
- exactly one Agent process;
- exactly one Bridge process;
- pending DetectionEvents = 0;
- current quota maximum / accepted / remaining;
- no existing publisher for the target camera path;
- snapshot capture/upload disabled;
- intended `source_id` → CameraId mapping;
- the exact input filename exists;
- the exact FFmpeg executable exists.

### Post-test checks

Confirm:

- all test publishers stopped;
- no orphan wrapper or child process;
- MediaMTX path inactive;
- pending DetectionEvents drained to zero;
- expected Alerts delivered;
- no duplicate Alert;
- snapshots remain absent;
- Agent and Bridge PIDs remain stable.

### Instruction precedence

These streaming instructions are authoritative for this project. If an older note, prompt or script
conflicts with them:

- use the real Scoop FFmpeg executable;
- use `Start-Process -PassThru`;
- terminate with `taskkill /PID <pid> /T /F`;
- default genuine detection tests to bounded, non-looping input.