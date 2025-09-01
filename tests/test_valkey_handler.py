"""Tests for AsyncValkeyHandler."""

import asyncio
import logging
import pytest
from glide import GlideClient, GlideClientConfiguration, NodeAddress, MinId, MaxId
from scietex.logging import (
    AsyncValkeyHandler,
)  # Replace with actual module path


@pytest.mark.asyncio
async def test_valkey_handler_logs_to_stream():
    """Testing logging to stream."""
    # Configuration for the Valkey connection and stream
    stream_name = "test_log_stream"
    client_config: GlideClientConfiguration = GlideClientConfiguration([NodeAddress()])

    valkey_client = await GlideClient.create(client_config)

    # Clear the test stream if it exists
    await valkey_client.delete([stream_name])
    service_name = "TestLogger"
    worker_id = 1
    # Create the Valkey log handler
    handler = AsyncValkeyHandler(
        service_name=service_name,
        worker_id=worker_id,
        stream_name=stream_name,
        valkey_config=client_config,
    )
    await handler.start_logging()  # Start the handler logging workers

    # Setup logger and attach the handler
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Log a test message
    messages_number = 10
    test_messages = [f"Test Valkey log message {i}" for i in range(messages_number)]
    for message in test_messages:
        logger.info(message)

    # Allow some time for the worker to process the log message
    await asyncio.sleep(1)

    messages = await valkey_client.xrange(
        stream_name, MinId(), MaxId(), count=messages_number
    )
    assert len(messages) == messages_number, "No messages found in the Valkey stream."
    messages_data = iter(messages.values())

    # Fetch the latest entry from the Valkey stream
    for message in test_messages:

        # Decode the message data from bytes to strings
        message_data = next(messages_data)
        print(message_data)
        decoded_message_data = {
            key.decode("utf-8"): value.decode("utf-8") for key, value in message_data
        }
        print(decoded_message_data)
        # Check the contents of the log entry
        assert (
            decoded_message_data["message"] == message
        ), "Valkey logger:Log message data mismatch."
        assert (
            decoded_message_data["level"] == "INF"
        ), "Valkey logger: Log level mismatch."
        assert (
            decoded_message_data["name"] == f"{service_name}:{worker_id}"
        ), "Valkey logger:Logger name mismatch."

    # Clean up
    await handler.stop_logging()
    await valkey_client.delete([stream_name])  # Clear the test stream after the test
    await valkey_client.close()  # Close the Valkey client connection
