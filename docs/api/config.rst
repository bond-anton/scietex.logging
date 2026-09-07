Configuration Types
===================

The frozen dataclasses below are the single source of truth for handler
options. ``LoggingConfig`` holds the shared machinery options; the backend
configs mirror the scalar option surface of their respective clients.

.. autoclass:: scietex.logging.LoggingConfig
   :no-index:

.. autoclass:: scietex.logging.RedisConfig
   :no-index:

.. autoclass:: scietex.logging.ValkeyConfig
   :no-index:

.. autoclass:: scietex.logging.MqttConfig
   :no-index:
