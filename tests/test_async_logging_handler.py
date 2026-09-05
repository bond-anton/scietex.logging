"""Tests for the AsyncLoggingHandler pure machinery base."""

import pytest

from scietex.logging import AsyncLoggingHandler


class BareHandler(AsyncLoggingHandler):
    """Handler that registers no backend, proving the base owns no sink."""


def test_pure_handler_owns_no_backend():
    """The base machinery holds no queue, worker, or drain hook on its own."""
    handler = BareHandler(service_name="TestService", worker_id=1)

    assert handler.log_queues == {}
    assert handler.log_worker_factories == []
    assert handler._drain_hooks == []


@pytest.mark.asyncio
async def test_pure_handler_starts_and_stops_cleanly():
    """A backend-less handler starts and stops without any queue activity."""
    handler = BareHandler(service_name="TestService", worker_id=1)

    await handler.start_logging()
    assert handler.logging_accept_event.is_set()
    assert handler.logging_running_event.is_set()
    assert handler.log_workers_tasks == []

    await handler.stop_logging()
    assert handler.log_queues == {}
    assert not handler.logging_accept_event.is_set()
    assert not handler.logging_running_event.is_set()


def test_unknown_kwarg_raises_type_error():
    """A typo'd kwarg fails loudly instead of being silently swallowed."""
    with pytest.raises(TypeError):
        BareHandler(service_name="TestService", worker_id=1, stdout_enabel=True)


def test_config_exposes_machinery_options():
    handler = BareHandler(
        service_name="Svc", worker_id=7, queue_maxsize=123, error_handler=lambda r, e: None
    )
    assert handler.config.service_name == "Svc"
    assert handler.config.worker_id == 7
    assert handler.config.queue_maxsize == 123
    assert handler.config.error_handler is not None
    assert handler.queue_maxsize == 123
    assert handler.error_handler is not None
