"""The DeepStream Bridge (IP-07 T-88, FS-05 §4.6).

A standalone application, never imported by ``weapon_detection_agent`` and never importing it
(FS-05 §4.6 binding rule 5). It owns the entire DeepStream/GStreamer/TensorRT/``pyds`` surface —
pipeline construction, inference, metadata extraction, OSD, RTSP output — and publishes raw,
unfiltered detection facts to the Agent over a Unix domain socket (ADR-005). It never validates,
deduplicates, resolves class names, or persists anything; that is exclusively the Agent's job.

Runs under its own Python 3.8 virtual environment (``/opt/weapon-detection/deepstream-bridge/venv``,
FS-05 §4.6) with ``pyds`` 1.1.6 and system GStreamer bindings — never the Agent's Python 3.11
environment.
"""

from __future__ import annotations
