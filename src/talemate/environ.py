"""Helpers for reading configuration from environment variables."""

import os
import sys

__all__ = ["env_str", "env_port"]


def env_str(var_name: str, default: str) -> str:
    """Read a string from an environment variable, falling back if unset/blank."""
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def env_port(var_name: str, default: int) -> int:
    """Read a port number from an environment variable with friendly errors."""
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    raw = raw.strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        print(
            f"\nERROR: Environment variable {var_name}={raw!r} is not a valid port number."
            f"\n       Set it to an integer between 1 and 65535, or unset it to use the default ({default}).\n"
        )
        sys.exit(1)
    if not (1 <= port <= 65535):
        print(
            f"\nERROR: Environment variable {var_name}={port} is out of range."
            f"\n       Port numbers must be between 1 and 65535.\n"
        )
        sys.exit(1)
    return port
