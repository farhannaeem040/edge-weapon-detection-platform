"""Production cutover preflight CLI (IP-07 T-90, task items 2/6/11).

``python -m weapon_detection_agent.detection.cutover_preflight`` — a read-only command the
production preflight script (``deployment/jetson/deepstream/bridge/preflight-cutover.sh``) invokes
through the Agent's own venv. It loads settings exactly as the real Agent would
(``load_settings()``, which runs the T-90 incompatible-configuration ``model_validator``
automatically) and, if that passes, runs the profile/class-label preflight
(``profile_preflight.py``). Every value comes from the ``WDA_``-prefixed environment the caller
sets — never a second, independent parsing of `agent.env` here, so there is exactly one
settings-loading code path in the whole Agent codebase.

**Never persistent.** Loading settings performs no filesystem I/O (`AgentSettings`'s own documented
invariant); `run_profile_preflight` only reads files, never writes one; this module never starts
`asyncio`, never binds a socket, never touches SQLite. Exit code is the only externally-visible
result besides stdout/stderr text — 0 only when every check passes, non-zero otherwise (2 for a
settings/configuration failure, 3 for a profile/class-label failure).

**Redaction.** Every printed line is either a fixed label or a field name — never a configured
*value* (mirrors `ConfigurationError`'s own `_describe` discipline). `activation_key` is not even a
field this command touches.
"""

from __future__ import annotations

import sys

from weapon_detection_agent.config.settings import ConfigurationError, load_settings
from weapon_detection_agent.detection.profile_preflight import run_profile_preflight

EXIT_OK = 0
EXIT_SETTINGS_INVALID = 2
EXIT_PROFILE_INVALID = 3


def run(argv: list[str] | None = None) -> int:  # noqa: ARG001 - argv accepted for CLI symmetry, unused
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"PREFLIGHT_FAIL settings: {exc}", file=sys.stderr)
        return EXIT_SETTINGS_INVALID

    print("PREFLIGHT_OK settings: configuration combination is valid")
    print(f"PREFLIGHT_INFO deepstream_enabled={settings.deepstream_enabled}")
    print(f"PREFLIGHT_INFO detection_events_enabled={settings.detection_events_enabled}")
    print(f"PREFLIGHT_INFO deepstream_model_profile={settings.deepstream_model_profile}")

    if not settings.detection_events_enabled:
        print("PREFLIGHT_OK profile: skipped (detection events disabled)")
        return EXIT_OK

    result = run_profile_preflight(settings)
    if not result:
        for problem in result.problems:
            print(f"PREFLIGHT_FAIL profile[{problem.check}]: {problem.detail}", file=sys.stderr)
        return EXIT_PROFILE_INVALID

    print(f"PREFLIGHT_OK profile: {result.class_count} class(es) resolved")
    if result.duplicate_labels:
        duplicates = ", ".join(result.duplicate_labels)
        print(f"PREFLIGHT_WARN profile: duplicate labels present: {duplicates}")

    return EXIT_OK


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
