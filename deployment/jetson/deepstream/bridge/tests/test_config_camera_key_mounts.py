"""FS-12 / IP-14 T-283 — the Bridge accepts CameraKey-based annotated-output mounts.

The Bridge is deliberately generic: it validates that an ``output-path`` is a *safe relative RTSP
mount* and nothing more. It does not know what a CameraKey is, does not parse one, and never uses one
as a detection identity — ``camera-id`` remains the only identity it reports. These tests pin that
separation down, so a future change to the key grammar cannot silently require a Bridge change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepstream_bridge.config import load_bridge_config
from deepstream_bridge.errors import BridgeConfigurationError

FRONT_ID = "2613b331-8783-4d51-903a-3e41a979a14c"
REAR_ID = "ad8a1f09-7fba-4794-8f73-63c7e2c57c92"


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _source(index: int, output_path: str, camera_id: str) -> str:
    return (
        f"[source{index}]\ntype=4\n"
        f"uri=rtsp://camera.example.invalid:554/s{index}\ngpu-id=0\n"
        f"output-path={output_path}\n"
        f"camera-id={camera_id}\n\n"
    )


def _app_config(tmp_path: Path, sources: str) -> Path:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = (
        "[application]\nenable-perf-measurement=1\n\n"
        "[tiled-display]\nenable=0\n\n"
        "[streammux]\nlive-source=1\nbatch-size=1\nwidth=1280\nheight=720\n\n"
        f"[primary-gie]\nenable=1\ngie-unique-id=1\nconfig-file={infer}\n\n"
        "[osd]\nenable=1\n\n"
        "[sink0]\nenable=1\ntype=4\nrtsp-port=8554\nudp-port=5400\ncodec=1\n\n"
        f"{sources}"
    )
    return _write(tmp_path / "deepstream-app.txt", content)


# --- Accepted CameraKey mounts -----------------------------------------------------------------


@pytest.mark.parametrize(
    "output_path",
    ["cameras/front-camera", "cameras/rear-entrance", "cameras/abc-123", "cameras/a1"],
)
def test_camera_key_mounts_are_accepted(tmp_path: Path, output_path: str) -> None:
    config = load_bridge_config(_app_config(tmp_path, _source(0, output_path, FRONT_ID)))

    assert config.sources[0].output_path == output_path


def test_legacy_guid_mount_is_still_accepted(tmp_path: Path) -> None:
    """The Bridge is format-agnostic, which is exactly why the GUID→key rollout needs no Bridge
    change and why a rollback to GUID paths also needs none."""
    config = load_bridge_config(_app_config(tmp_path, _source(0, f"cameras/{FRONT_ID}", FRONT_ID)))

    assert config.sources[0].output_path == f"cameras/{FRONT_ID}"


# --- Rejected paths (path safety, not key grammar) ---------------------------------------------


@pytest.mark.parametrize(
    "output_path",
    [
        "cameras/front camera",       # whitespace: unusable in an RTSP request URI
        "cameras/../etc",             # traversal
        "/cameras/front-camera",      # absolute
        "rtsp://host/cameras/front",  # absolute URL
        "cameras/front?token=x",      # query
        "cameras/front#frag",         # fragment
        "cameras//front",             # empty segment
        "cameras\\front",             # backslash
    ],
)
def test_unsafe_output_paths_are_rejected(tmp_path: Path, output_path: str) -> None:
    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(_app_config(tmp_path, _source(0, output_path, FRONT_ID)))


def test_uppercase_path_is_accepted_by_the_bridge_by_design(tmp_path: Path) -> None:
    """FS-12 §3 key *grammar* is Backend/Agent policy, not a Bridge concern.

    ``cameras/Front-Camera`` is not a valid CameraKey, but it *is* a perfectly safe relative mount,
    so the Bridge accepts it. Rejecting it here would mean teaching the Bridge the key grammar —
    Camera-specific domain logic the plan explicitly avoids. The Backend rejects such a key at
    creation and the Agent re-checks it (``invalid_camera_key``), so no such path can ever be
    generated in practice; this test documents *where* that rule lives rather than duplicating it.
    """
    config = load_bridge_config(_app_config(tmp_path, _source(0, "cameras/Front-Camera", FRONT_ID)))

    assert config.sources[0].output_path == "cameras/Front-Camera"


# --- Mount cardinality and uniqueness -----------------------------------------------------------


def test_duplicate_camera_key_mounts_are_rejected(tmp_path: Path) -> None:
    """Two sources on one mount would publish one Camera's frames on the other's URL."""
    sources = _source(0, "cameras/front-camera", FRONT_ID) + _source(
        1, "cameras/front-camera", REAR_ID
    )

    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(_app_config(tmp_path, sources))


def test_one_camera_yields_one_mount(tmp_path: Path) -> None:
    config = load_bridge_config(_app_config(tmp_path, _source(0, "cameras/front-camera", FRONT_ID)))

    assert len([s for s in config.sources if s.output_path]) == 1


def test_two_cameras_yield_two_distinct_mounts(tmp_path: Path) -> None:
    sources = _source(0, "cameras/front-camera", FRONT_ID) + _source(
        1, "cameras/rear-entrance", REAR_ID
    )

    config = load_bridge_config(_app_config(tmp_path, sources))

    assert [s.output_path for s in config.sources] == [
        "cameras/front-camera",
        "cameras/rear-entrance",
    ]


@pytest.mark.parametrize("count", [1, 2, 3])
def test_n_cameras_yield_n_mounts(tmp_path: Path, count: int) -> None:
    sources = "".join(_source(i, f"cameras/cam-{i}", f"{FRONT_ID[:-1]}{i}") for i in range(count))

    config = load_bridge_config(_app_config(tmp_path, sources))

    assert len(config.sources) == count
    assert len({s.output_path for s in config.sources}) == count


# --- Identity separation (FS-12 §2) -------------------------------------------------------------


def test_camera_id_is_the_identity_not_the_mount(tmp_path: Path) -> None:
    """The key names the stream; the GUID names the Camera. The Bridge only reports the GUID."""
    sources = _source(0, "cameras/front-camera", FRONT_ID) + _source(
        1, "cameras/rear-entrance", REAR_ID
    )

    config = load_bridge_config(_app_config(tmp_path, sources))

    assert config.sources[0].camera_id == FRONT_ID
    assert config.sources[1].camera_id == REAR_ID
    # The mount path is never mistaken for the identity.
    assert config.sources[0].camera_id != config.sources[0].output_path


def test_camera_key_is_not_parsed_as_a_bridge_concept(tmp_path: Path) -> None:
    """There is deliberately no `camera_key` on SourceConfig — the Agent writes `camera-key` into the
    generated file for operator readability only, and the Bridge ignores it."""
    source = (
        "[source0]\ntype=4\nuri=rtsp://camera.example.invalid:554/s0\ngpu-id=0\n"
        "output-path=cameras/front-camera\n"
        f"camera-id={FRONT_ID}\n"
        "camera-key=front-camera\n\n"
    )

    config = load_bridge_config(_app_config(tmp_path, source))

    assert not hasattr(config.sources[0], "camera_key")
    assert config.sources[0].camera_id == FRONT_ID
