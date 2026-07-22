"""Agent-side device-credential validation (IP-05 T-57).

The Agent's periodic, detect-only check that its stored Device ID + shared secret are still the
device's current, active credentials, against ``POST /api/v1/device/credentials/validate``
(FS-02 §10.5, ADR-017). This package owns only the HTTP client and its typed outcome; scheduling
(T-59), local persistence/lock (T-58), the operational-state coordinator (T-60), and startup policy
(T-61) live elsewhere. It is deliberately separate from the activation client — activation and
periodic validation have different contracts and lifecycle semantics.
"""
