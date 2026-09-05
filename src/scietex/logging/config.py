"""Typed configuration objects and shared stdlib-only helpers for scietex.logging.

This module is the neutral leaf of the package: it hosts the configuration
dataclasses (``LoggingConfig``, ``RedisConfig``, ``ValkeyConfig``) alongside the
cross-module helpers ``validate_queue_maxsize``, ``level_abbreviation``, and
``optional_dependency_error``. It imports only the standard library, so formatter
and broker handlers can depend on it without creating an import cycle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoggingConfig:
    """Shared machinery options for every handler.

    This is the single runtime source of truth: handlers read these fields at
    work time. The flat attributes each handler exposes (``queue_maxsize``,
    ``stdout_enable``, ``error_handler``) are read-only aliases over this object.

    Attributes:
        service_name (str): Service name used in the formatter's worker_name.
        worker_id (int): Worker id used in the formatter's worker_name.
        error_handler (Callable | None): Delivery-error callback ``(record, exc)``.
        queue_maxsize (int): Bound for every backend queue (default 10000).
        stdout_enable (bool): Whether AsyncBaseHandler registers the console backend.
        backend_config (RedisConfig | ValkeyConfig | None): Backend-specific config,
            or None for the console-only handlers.
    """

    service_name: str = "Service"
    worker_id: int = 1
    error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None
    queue_maxsize: int = 10000
    stdout_enable: bool = True
    backend_config: RedisConfig | ValkeyConfig | None = None


@dataclass(frozen=True)
class RedisConfig:
    """Connection settings for the Redis backend.

    Mirrors the option surface of ``redis.Redis`` so that ``RedisConfig(**raw)``
    never rejects a legitimate client option. ``host``/``port``/``db`` are the
    common connection trio; the remaining fields are optional client options
    passed through to ``redis.Redis`` unchanged. Object-valued expert options
    (``connection_pool``, ``retry``, ``credential_provider``, ``cache``,
    ``event_dispatcher``, ...) are intentionally not modeled here.

    Attributes:
        host (str): Redis server host (default "localhost").
        port (int): Redis server port (default 6379).
        db (int): Redis database number (default 0).
    """

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None
    socket_timeout: float | None = None
    socket_connect_timeout: float | None = None
    socket_read_size: int = 32768
    socket_keepalive: bool = False
    socket_keepalive_options: dict | None = None
    unix_socket_path: str | None = None
    encoding: str = "utf-8"
    encoding_errors: str = "strict"
    decode_responses: bool = False
    retry_on_error: list | None = None
    ssl: bool = False
    ssl_keyfile: str | None = None
    ssl_certfile: str | None = None
    ssl_cert_reqs: str | None = None
    ssl_include_verify_flags: list | None = None
    ssl_exclude_verify_flags: list | None = None
    ssl_ca_certs: str | None = None
    ssl_ca_path: str | None = None
    ssl_ca_data: str | None = None
    ssl_check_hostname: bool = False
    ssl_password: str | None = None
    ssl_min_version: str | None = None
    ssl_ciphers: str | None = None
    max_connections: int | None = None
    single_connection_client: bool = False
    health_check_interval: int = 0
    client_name: str | None = None
    protocol: int | None = None
    legacy_responses: bool = True


@dataclass(frozen=True)
class ValkeyConfig:
    """Connection settings for the Valkey backend.

    Mirrors the scalar plain options of ``GlideClientConfiguration`` so that
    ``ValkeyConfig(**raw)`` never rejects a legitimate scalar client option.
    Enum-typed options (``read_from``, ``protocol``, ``node_discovery_mode``) and
    object-valued options (``credentials``, ``reconnect_strategy``,
    ``pubsub_subscriptions``, ``advanced_config``, ``compression``,
    ``client_side_cache``, ``address_resolver``, ``client_circuit_breaker``) are
    intentionally not modeled here. Optional fields default to ``None``, meaning
    "let glide apply its own default".

    Attributes:
        addresses (list[tuple[str, int]]): (host, port) pairs for the Valkey nodes.
            Defaults to a single localhost:6379 node.
    """

    addresses: list[tuple[str, int]] = field(default_factory=lambda: [("localhost", 6379)])
    use_tls: bool | None = None
    request_timeout: int | None = None
    database_id: int | None = None
    client_name: str | None = None
    inflight_requests_limit: int | None = None
    client_az: str | None = None
    lazy_connect: bool | None = None
    read_only: bool | None = None


def validate_queue_maxsize(value: int) -> int:
    """Return ``value`` if it is a positive int, else raise ValueError."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"queue_maxsize must be a positive int, got {value!r}")
    return value


def level_abbreviation(log_level: int) -> str:
    """
    Map logging levels to 3-letter abbreviations.

    Args:
        log_level (int): The integer log level (e.g., logging.DEBUG, logging.INFO).

    Returns:
        str: A 3-letter abbreviation corresponding to the log level, or a 3-digit code
             if the level is unrecognized.
    """
    level_map: dict[int, str] = {
        logging.DEBUG: "DBG",
        logging.INFO: "INF",
        logging.WARNING: "WRN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "CRT",
    }
    return level_map.get(log_level, f"{log_level:03d}")


def optional_dependency_error(module_name: str, extra: str) -> str:
    """Return the descriptive ImportError message for a missing optional backend dependency."""
    return (
        f"The '{module_name}' module is required to use this feature. "
        f"Please install it by running:\n\n    pip install scietex.logging[{extra}]\n"
    )
