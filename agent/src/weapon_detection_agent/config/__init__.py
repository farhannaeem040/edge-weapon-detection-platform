"""Agent bootstrap configuration (IP-02 §6).

This subpackage holds the Agent's own startup configuration — the settings model and its loader.
It is deliberately small: it reads environment variables into one immutable, validated object and
does nothing else. It performs no network, filesystem, database, or Jetson I/O, so importing it is
always safe.

The Backend-synchronized *operational* configuration (FR-SYN-005/006) is a different concern and is
not implemented here (IP-02 OI-2). The filesystem-layout resolution sketched for this subpackage in
IP-02 §4 (`paths.py`) is a later task (T-34) and is not present yet.
"""
