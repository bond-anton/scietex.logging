"""Custom in-memory broker backend example."""

import asyncio
import logging

from scietex.logging import AsyncBrokerHandler


class InMemoryHandler(AsyncBrokerHandler):
    """Broker handler that appends records to an in-memory list."""

    def __init__(self, *args, **kwargs):
        self.records: list[dict[str, str]] = []
        super().__init__(*args, **kwargs)

    async def connect(self):
        # No external service exists, so a plain sentinel marks the client as connected.
        self.client = object()

    async def disconnect(self):
        self.client = None

    async def send_message(self, record):
        self.records.append(record)


async def main():
    """Main function."""
    logger = logging.getLogger("MemoryLogger")
    logger.setLevel(logging.DEBUG)

    # stdout_enable=False drops the inherited console backend, so this handler is
    # broker-only: every record reaches send_message and lands in self.records.
    handler = InMemoryHandler(
        queue_name="memory",
        service_name="MemoryService",
        worker_id=1,
        stdout_enable=False,
    )
    logger.addHandler(handler)

    await handler.start_logging()

    logger.info("In-memory info message.")
    logger.error("In-memory error message.")

    await handler.stop_logging()

    # Each captured record is a dict[str, str] keyed by level, message, name, time.
    for record in handler.records:
        print(record)


asyncio.run(main())
