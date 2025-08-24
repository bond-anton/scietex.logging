"""Tests for AsyncRedisHandler."""

import asyncio
import logging
import pytest
from redis.asyncio import Redis
from scietex.logging.redis_handler import (
    AsyncRedisHandler,
)  # Replace with actual module path


@pytest.mark.asyncio
async def test_redis_handler_logs_to_stream():
    """Testing logging to stream."""
    # Configuration for the Redis connection and stream
    stream_name = "test_log_stream"
    redis_config = {"host": "localhost", "port": 6379, "db": 0}

    # Initialize Redis client to interact with the stream directly
    redis_client = Redis(**redis_config)

    # Clear the test stream if it exists
    await redis_client.delete(stream_name)
    service_name = "TestLogger"
    worker_id = 1
    # Create the Redis log handler
    handler = AsyncRedisHandler(
        service_name=service_name,
        worker_id=worker_id,
        stream_name=stream_name,
        redis_config=redis_config,
    )
    await handler.start_logging()  # Start the handler logging workers

    # Setup logger and attach the handler
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Log a test message
    test_message = "Test log message"
    logger.info(test_message)

    # Allow some time for the worker to process the log message
    await asyncio.sleep(1)

    # Fetch the latest entry from the Redis stream
    messages = await redis_client.xrange(stream_name, count=1)
    print(messages)
    assert len(messages) == 1, "No messages found in the Redis stream."

    # Decode the message data from bytes to strings
    _, message_data = messages[0]
    decoded_message_data = {
        key.decode("utf-8"): value.decode("utf-8")
        for key, value in message_data.items()
    }
    print(decoded_message_data)
    # Check the contents of the log entry
    assert (
        decoded_message_data["message"] == test_message
    ), "Log message content mismatch."
    assert decoded_message_data["level"] == "INF", "Log level mismatch."
    assert (
        decoded_message_data["name"] == f"{service_name}:{worker_id}"
    ), "Logger name mismatch."

    # Clean up
    await handler.stop_logging()
    await redis_client.delete(stream_name)  # Clear the test stream after the test
    await redis_client.aclose()  # Close the Redis client connection
