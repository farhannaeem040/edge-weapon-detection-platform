"""Scaffold smoke tests for the Jetson Agent (IP-02 T-31).

These prove only that the scaffold is wired up correctly: the package imports, the FastAPI
application is constructed with the right metadata, and none of this requires a Jetson, a GPU,
DeepStream, a Backend, a database, or network access. They intentionally assert the *absence* of
any operational endpoint, because T-31 defines none.
"""

import importlib

import weapon_detection_agent
from weapon_detection_agent import __version__
from weapon_detection_agent.main import app


def test_package_imports() -> None:
    # The package imports and exposes a version string.
    assert isinstance(__version__, str)
    assert __version__


def test_application_is_constructed() -> None:
    # The FastAPI application object exists and is importable.
    from fastapi import FastAPI

    assert isinstance(app, FastAPI)


def test_application_metadata() -> None:
    # Title and version metadata are configured, and the version matches the package version so the
    # two cannot drift apart.
    assert app.title == "Weapon Detection Agent"
    assert app.version == __version__


def test_no_operational_routes_defined() -> None:
    # T-31 is scaffolding only: no Agent endpoint is approved or implemented yet (IP-02 §2.2, OI-3).
    # Only FastAPI's built-in OpenAPI/docs routes may exist; no custom path is registered.
    builtin_paths = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    custom_paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", None) is not None and route.path not in builtin_paths
    }
    assert custom_paths == set()


def test_import_requires_no_jetson_specific_dependency() -> None:
    # Importing the application must not pull in any Jetson/hardware/data-plane package. If a later
    # edit accidentally added such an import at module load time, importing main would raise
    # ModuleNotFoundError on a plain workstation and this reload would fail.
    forbidden = {
        "pyds",  # DeepStream Python bindings
        "cv2",  # OpenCV
        "tensorrt",
        "pycuda",
        "Jetson",  # Jetson.GPIO
    }
    import sys

    module = importlib.reload(importlib.import_module("weapon_detection_agent.main"))
    assert module.app.title == "Weapon Detection Agent"
    assert forbidden.isdisjoint(sys.modules.keys())
    assert weapon_detection_agent is not None
