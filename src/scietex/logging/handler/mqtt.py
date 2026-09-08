"""Asynchronous MQTT logging handler for non-blocking logging."""

from ..config import MqttConfig, optional_dependency_error

try:
    import aiomqtt
except ImportError as e:
    raise ImportError(optional_dependency_error("aiomqtt", "mqtt"), name="aiomqtt") from e

import json
import logging
from collections.abc import Callable

from ..async_logging_handler import _QUEUE_MQTT
from .broker import AsyncBrokerHandler


class AsyncMqttHandler(AsyncBrokerHandler):
    """
    Asynchronous MQTT logging handler for non-blocking logging.

    This handler publishes log records as JSON payloads to an MQTT topic,
    enabling asynchronous logging without blocking the main application. The
    handler maintains a separate worker to process MQTT log records queued in an
    asyncio queue.

    Attributes:
        topic (str): The MQTT topic to which log entries are published.
        qos (int): The MQTT QoS level (0, 1, or 2) used for publication.
        retain (bool): Whether published messages are retained by the broker.
        client (aiomqtt.Client | None): The MQTT client connection, or None if not connected.

    Methods:
        connect():
            Connect to MQTT asynchronously.
        disconnect():
            Disconnect from MQTT asynchronously.
        send_message():
            Send log record to MQTT asynchronously.
    """

    def __init__(
        self,
        topic: str,
        *,
        mqtt_config: dict | None = None,
        qos: int = 0,
        retain: bool = False,
        client: aiomqtt.Client | None = None,
        error_handler: Callable[[logging.LogRecord | None, Exception], None] | None = None,
        queue_maxsize: int = 10000,
    ) -> None:
        """
        Initialize the asynchronous MQTT logging handler.

        Args:
            topic (str): The MQTT topic to which log records are published.
            mqtt_config (dict, optional): Configuration dictionary for the MQTT connection.
                Defaults to {"host": "localhost", "port": 1883}. Keys are converted into a
                typed ``MqttConfig`` stored as ``self.backend_config``, which is the
                single source passed to ``aiomqtt.Client`` by ``connect()``. Mutually
                exclusive with ``client``.
            qos (int): The MQTT QoS level (0, 1, or 2) used for publication. Defaults to 0
                (at-most-once, fire-and-forget). QoS 1/2 trade throughput for delivery
                guarantees.
            retain (bool): Whether published messages are retained by the broker. Defaults
                to False.
            client (aiomqtt.Client | None): An externally-managed MQTT client to use instead
                of building one in ``connect()``. When provided, the handler never closes it
                — the caller owns its lifetime and recovery. The injected client must already
                be connected (inside its ``async with`` context) before ``start_logging()``,
                because the handler never enters the context on an injected client. Mutually
                exclusive with ``mqtt_config``. Defaults to None.
            error_handler (callable, optional): Callback invoked with ``(record, exc)``
                when a log record cannot be delivered. Defaults to None, in which case
                errors are reported via the ``scietex.logging`` module logger.
            queue_maxsize (int): Maximum number of records each backend queue can hold.
                Defaults to 10000.

        Attributes:
            topic (str): The MQTT topic to which log entries are published.
            client (aiomqtt.Client | None): The MQTT client connection, or None if not connected.

        Raises:
            TypeError: If an unknown keyword argument is passed.
            ValueError: If both ``client`` and ``mqtt_config`` are provided.
        """
        # When a client is injected, connect() is never called so the config is
        # unused; pass None unless the caller explicitly supplied a config dict, in
        # which case it flows through so the base raises on the contradiction.
        if client is not None:
            backend_cfg = MqttConfig(**mqtt_config) if mqtt_config is not None else None
        else:
            backend_cfg = MqttConfig(**(mqtt_config or {"host": "localhost", "port": 1883}))
        super().__init__(
            queue_name=_QUEUE_MQTT,
            error_handler=error_handler,
            queue_maxsize=queue_maxsize,
            backend_config=backend_cfg,
            client=client,
        )
        self.topic = topic
        self.qos = qos
        self.retain = retain

    async def connect(self) -> None:
        """
        Connect to MQTT asynchronously.

        Builds the client from ``self.backend_config`` (the typed
        ``MqttConfig``, the single source of truth). ``host`` is translated to
        aiomqtt's ``hostname`` kwarg, and ``None``-valued fields are dropped so
        aiomqtt applies its own defaults. The client is entered as an async
        context manager; a failed connection raises so the worker can report it
        and retry.

        Returns:
            None
        """
        if self.client is None:
            cfg = self._config_dict()
            hostname = cfg.pop("host")
            kwargs = {k: v for k, v in cfg.items() if v is not None}
            client = aiomqtt.Client(hostname=hostname, **kwargs)
            await client.__aenter__()
            self.client = client

    async def disconnect(self) -> None:
        """
        Disconnect from MQTT asynchronously.
        """
        if self.client is not None:
            await self.client.__aexit__(None, None, None)
            self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        """
        Send log record to MQTT asynchronously.

        Args:
            record (dict[str, str]): The log record to send as a dictionary.

        Returns:
            None
        """
        if self.client is None:
            raise RuntimeError("MQTT client is not connected; call connect() first.")
        await self.client.publish(self.topic, json.dumps(record), qos=self.qos, retain=self.retain)
