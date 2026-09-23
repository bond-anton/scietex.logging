"""AsyncMqttHandler usage example."""

# examples/mqtt_logging.py

import asyncio
import logging

from scietex.logging import AsyncMqttHandler


async def main():
    """Main function."""
    # Initialize a logger
    logger = logging.getLogger("ExampleMqttLogger")
    logger.setLevel(logging.DEBUG)

    # Set up the asynchronous MQTT logging handler
    mqtt_handler = AsyncMqttHandler(
        topic="example/log/topic",
        mqtt_config={"host": "localhost", "port": 1883},
        # Discard undelivered log messages after one hour (MQTT 5).
        message_expiry=3600,
    )
    logger.addHandler(mqtt_handler)

    # Start the asynchronous logging tasks
    await mqtt_handler.start_logging()

    # Log some messages at various levels
    logger.info("This is an info message sent to MQTT.")
    logger.error("This is an error message sent to MQTT.")

    # Stop the logging and cleanup
    await mqtt_handler.stop_logging()


# Run the example
asyncio.run(main())
