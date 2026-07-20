"""Agent runtime composition (IP-02 T-39): the FastAPI startup workflow and the published state.

This subpackage wires the T-32–T-38 foundations into a real FastAPI lifespan
(:func:`create_lifespan`) and exposes the small, secret-free :class:`AgentRuntime` it publishes
on ``app.state.runtime`` once startup succeeds. It adds no HTTP route, starts no
DeepStream/heartbeat, and writes no ConfigCache. Importing it performs no I/O and runs no startup.
"""

from weapon_detection_agent.runtime.startup import create_lifespan
from weapon_detection_agent.runtime.state import AgentRuntime, get_runtime

__all__ = ["AgentRuntime", "create_lifespan", "get_runtime"]
