"""AsyncMqttHandler usage example."""

# examples/mqtt_logging.py

import asyncio
import logging

from scietex.logging.mqtt_handler import AsyncMqttHandler


async def main():
    """Main function."""
    # Initialize a logger
    logger = logging.getLogger("ExampleMqttLogger")
    logger.setLevel(logging.DEBUG)

    # Set up the asynchronous MQTT logging handler
    mqtt_handler = AsyncMqttHandler(
        topic="example/log/topic",
        service_name="MqttService",
        instance_id="4",
        mqtt_config={"host": "localhost", "port": 1883},
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
