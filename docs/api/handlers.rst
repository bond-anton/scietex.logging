Handlers, Formatter, and Console Backend
========================================

The handler hierarchy
---------------------

``scietex.logging`` builds on the standard-library ``logging.Handler``. The
public hierarchy is:

``AsyncLoggingHandler`` (pure queue/worker machinery, no sink)
  -> ``AsyncBaseHandler`` (registers the console backend when
  ``stdout_enable=True``)
  -> ``AsyncBrokerHandler`` (abstract broker base)
  -> ``AsyncRedisHandler`` / ``AsyncValkeyHandler`` / ``AsyncMqttHandler``.

.. autoclass:: scietex.logging.AsyncLoggingHandler
   :no-index:

.. autoclass:: scietex.logging.AsyncBaseHandler
   :no-index:

.. autoclass:: scietex.logging.AsyncBrokerHandler
   :no-index:

.. autoclass:: scietex.logging.AsyncRedisHandler
   :no-index:

.. autoclass:: scietex.logging.AsyncValkeyHandler
   :no-index:

.. autoclass:: scietex.logging.AsyncMqttHandler
   :no-index:

Console backend
---------------

.. autoclass:: scietex.logging.ConsoleBackend
   :no-index:

Formatter
---------

.. autoclass:: scietex.logging.ScietexFormatter
   :no-index:
