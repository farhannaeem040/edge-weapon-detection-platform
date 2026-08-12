"""Server-driven Device Camera configuration (FS-11, IP-13).

The Backend's ``Camera`` entity, not local static config, is the authoritative source of DeepStream
pipeline input once :data:`~weapon_detection_agent.config.settings.AgentSettings.
device_config_enabled` is ``True``. See :mod:`weapon_detection_agent.configuration.coordinator` for
the lifecycle component that ties fetch → validate → cache → apply together.
"""

from __future__ import annotations
