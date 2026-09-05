"""Typed configuration objects for scietex.logging handlers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LoggingConfig:
    """Shared machinery options for every handler.

    Attributes:
        service_name (str): Service name used in the formatter's worker_name.
        worker_id (int): Worker id used in the formatter's worker_name.
        error_handler (Callable | None): Delivery-error callback ``(record, exc)``.
        queue_maxsize (int): Bound for every backend queue (default 10000).
        stdout_enable (bool): Whether AsyncBaseHandler registers the console backend.
        backend_config (Any | None): Backend-specific config (RedisConfig, ValkeyConfig,
            or None for the pure-machinery/console-only handlers).
    """

    service_name: str = "Service"
    worker_id: int = 1
    error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None
    queue_maxsize: int = 10000
    stdout_enable: bool = True
    backend_config: Any | None = None


@dataclass(frozen=True)
class RedisConfig:
    """Connection settings for the Redis backend.

    Attributes:
        host (str): Redis server host (default "localhost").
        port (int): Redis server port (default 6379).
        db (int): Redis database number (default 0).
    """

    host: str = "localhost"
    port: int = 6379
    db: int = 0


@dataclass(frozen=True)
class ValkeyConfig:
    """Connection settings for the Valkey backend.

    Attributes:
        addresses (list[tuple[str, int]]): (host, port) pairs for the Valkey nodes.
            Defaults to a single localhost:6379 node.
    """

    addresses: list[tuple[str, int]] = field(default_factory=lambda: [("localhost", 6379)])


def validate_queue_maxsize(value: int) -> int:
    """Return ``value`` if it is a positive int, else raise ValueError."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"queue_maxsize must be a positive int, got {value!r}")
    return value


def optional_dependency_error(module_name: str, extra: str) -> str:
    """Return the descriptive ImportError message for a missing optional backend dependency."""
    return (
        f"The '{module_name}' module is required to use this feature. "
        f"Please install it by running:\n\n    pip install scietex.logging[{extra}]\n"
    )
