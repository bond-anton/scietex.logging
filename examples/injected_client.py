"""Injecting an externally-managed Valkey client into AsyncValkeyHandler example.

Demonstrates the client-injection seam: instead of letting the handler build and
own its own connection, the application creates a ``GlideClient`` it manages and
passes it to the handler via ``client=``. The handler then never calls
``close()`` on that client — the caller owns its lifetime and recovery. After
``stop_logging()`` the client is still open, so the application can keep using
it (here, to read back the log stream) and closes it itself.
"""

import asyncio
import logging

from glide import GlideClient, GlideClientConfiguration, MaxId, MinId, NodeAddress

from scietex.logging.valkey_handler import AsyncValkeyHandler


async def main():
    """Main function."""
    stream_name = "injected_client_stream"
    logger = logging.getLogger("InjectedClientLogger")
    logger.setLevel(logging.DEBUG)

    # The application owns this client: it creates it, will close it, and may use
    # it for its own purposes (here, reading the log stream back).
    client_config = GlideClientConfiguration([NodeAddress(host="localhost", port=6379)])
    valkey_client = await GlideClient.create(client_config)
    await valkey_client.delete([stream_name])  # start from a clean stream

    # Inject the externally-managed client. No valkey_config is passed: the
    # handler builds no connection of its own and never closes this client.
    handler = AsyncValkeyHandler(
        stream_name=stream_name,
        service_name="InjectedService",
        instance_id="1",
        client=valkey_client,
    )
    logger.addHandler(handler)

    await handler.start_logging()

    logger.info("Info message via the injected client.")
    logger.error("Error message via the injected client.")

    # stop_logging drains and stops the worker but must NOT close the injected
    # client — the handler does not own it.
    await handler.stop_logging()

    # The client is still open: read the log stream back with it, then close it
    # ourselves. If the handler had closed it, this read would fail.
    messages = await valkey_client.xrange(stream_name, MinId(), MaxId())
    if messages is None:
        raise RuntimeError("No messages found in the log stream.")
    for message_id, fields in messages.items():
        decoded = {k.decode(): v.decode() for k, v in fields}
        print(f"{message_id.decode()}: {decoded['level']} {decoded['message']}")

    await valkey_client.delete([stream_name])  # clean up the stream
    await valkey_client.close()  # the host closes the client it owns


asyncio.run(main())
