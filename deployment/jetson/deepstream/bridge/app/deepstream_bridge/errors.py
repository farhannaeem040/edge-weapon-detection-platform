"""Typed error hierarchy for the DeepStream Bridge (IP-07 T-88, FS-05 §11).

Distinguishes the failure categories §11 requires the Bridge to handle and report distinctly:
configuration errors, missing GStreamer/``pyds``/``GstRtspServer`` bindings, pipeline element/link
failures, and fatal GStreamer bus errors (which must stop the Bridge with a non-zero exit code so
``DeepStreamProcessManager`` can restart it, per the existing IP-06 T-73 unexpected-exit detection).

Transport/socket failures are deliberately **not** part of this hierarchy — FS-05 §4.4/§11 makes
them non-fatal and rate-limited, handled entirely inside ``transport.py`` without ever propagating
to the pipeline/main-loop layer.
"""

from __future__ import annotations


class BridgeError(Exception):
    """Base class for every Bridge-specific error."""


class BridgeConfigurationError(BridgeError):
    """The DeepStream application config or profile ``infer-config.txt`` could not be parsed into a
    valid :class:`~deepstream_bridge.config.BridgeConfig` (FS-05 §4.3's configuration mapping)."""


class MissingGStreamerBindingsError(BridgeError):
    """``gi``/``Gst`` are not importable in this interpreter."""


class MissingPydsError(BridgeError):
    """``pyds`` is not importable in this interpreter (IP-07 T-80's venv is missing/broken)."""


class MissingGstRtspServerBindingsError(BridgeError):
    """``GstRtspServer`` (the ``gir1.2-gst-rtsp-server-1.0`` package) is not importable.

    Documented prerequisite gap (``deployment/jetson/deepstream/README.md``, "One gap found and
    deferred to T-88") — the package was confirmed absent from the real Jetson as of T-80. Raised
    only when the resolved config's RTSP-out branch is enabled; a fakesink-only pipeline never
    triggers this.
    """


class PipelineElementCreationError(BridgeError):
    """A required GStreamer element factory failed to create an element (missing plugin/module)."""


class PipelineLinkError(BridgeError):
    """Two pipeline elements or pads failed to link."""


class PipelineBusError(BridgeError):
    """A fatal GStreamer bus ``ERROR`` message was received; the Bridge must stop and exit non-zero
    (item 10: "A GStreamer bus error must stop the Bridge and return a non-zero exit code")."""
