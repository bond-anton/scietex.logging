"""Tests for the AsyncLoggingHandler pure machinery base."""

import asyncio

import pytest

from scietex.logging import AsyncLoggingHandler
from scietex.logging.async_logging_handler import BackendDrainResult, DrainStatus


class BareHandler(AsyncLoggingHandler):
    """Handler that registers no backend, proving the base owns no sink."""


def test_pure_handler_owns_no_backend():
    """The base machinery holds no queue, worker, drain hook, or reporter on its own."""
    handler = BareHandler(service_name="TestService", worker_id=1)

    assert handler.log_queues == {}
    assert handler.log_worker_factories == []
    assert handler._drain_hooks == []
    assert handler._status_reporters == []


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


def test_config_is_single_source_of_truth():
    """Runtime state reads self.config; the flat attributes are read-only aliases."""

    def err(record, exc):
        pass

    handler = BareHandler(service_name="Svc", worker_id=7, queue_maxsize=123, error_handler=err)

    # The flat aliases mirror config, which is authoritative.
    assert handler.queue_maxsize == handler.config.queue_maxsize == 123
    assert handler.error_handler is handler.config.error_handler
    assert handler.error_handler is err
    # Identity comes from config, not from parallel flat state.
    assert (
        handler.formatter.worker_name == f"{handler.config.service_name}:{handler.config.worker_id}"
    )

    # The aliases are read-only, so they cannot drift from the config that drives behavior.
    with pytest.raises(AttributeError):
        handler.queue_maxsize = 5
    with pytest.raises(AttributeError):
        handler.error_handler = None


@pytest.mark.asyncio
async def test_stop_logging_collects_results_and_reports():
    """stop_logging drains each backend, collects results, then reports them."""
    reported = []

    class ReportingHandler(AsyncLoggingHandler):
        def __init__(self):
            super().__init__()
            self.register_backend("a", asyncio.Queue(), self._noop_worker, self._drain_a)
            self.register_backend("b", asyncio.Queue(), self._noop_worker, self._drain_b)
            self.register_status_reporter(self._report)

        async def _report(self, results):
            reported.append(list(results))

        async def _noop_worker(self):
            while self.logging_running_event.is_set():
                await asyncio.sleep(0.01)

        async def _drain_a(self, timeout):
            return BackendDrainResult("a", DrainStatus.COMPLETED)

        async def _drain_b(self, timeout):
            return BackendDrainResult("b", DrainStatus.TIMEOUT)

    handler = ReportingHandler()
    await handler.start_logging()
    await handler.stop_logging(timeout=0.1)

    assert [r.name for r in reported[0]] == ["a", "b"]  # both backend results collected
    assert reported[0][0].status is DrainStatus.COMPLETED
    assert reported[0][1].status is DrainStatus.TIMEOUT
