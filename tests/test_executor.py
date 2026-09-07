"""Tests for the _WriteExecutor single-thread executor helper."""

import asyncio
import threading

import pytest

from scietex.logging._executor import _WriteExecutor


class SlowWriter:
    """Records the thread each call runs on and the write/close ordering."""

    def __init__(self, delay: float = 0.1) -> None:
        self.delay = delay
        self.writes: list[str] = []
        self.write_threads: list[str] = []
        self.close_threads: list[str] = []
        self.closed = False

    def write(self, text: str) -> int:
        self.write_threads.append(threading.current_thread().name)
        import time

        time.sleep(self.delay)  # simulate a blocking disk write
        self.writes.append(text)
        return len(text)

    def close(self) -> None:
        self.close_threads.append(threading.current_thread().name)
        self.closed = True


@pytest.mark.asyncio
async def test_run_executes_on_a_non_loop_thread():
    """run() executes the callable on a worker thread, not the event-loop thread."""
    executor = _WriteExecutor()
    loop_thread = threading.current_thread().name
    writer = SlowWriter(delay=0.01)

    await executor.run(lambda: writer.write("hello"))

    assert writer.writes == ["hello"]
    assert writer.write_threads[0] != loop_thread
    await executor.shutdown()


@pytest.mark.asyncio
async def test_run_serializes_write_then_close():
    """A close submitted after an in-flight write runs strictly after it."""
    executor = _WriteExecutor()
    writer = SlowWriter(delay=0.1)

    # Submit a write, let it start, then submit a close. On the single-thread
    # executor the close must run strictly after the write completes.
    write_task = asyncio.create_task(executor.run(lambda: writer.write("data")))
    await asyncio.sleep(0.02)
    close_task = asyncio.create_task(executor.run(writer.close))

    await write_task
    await close_task

    assert writer.writes == ["data"]
    assert writer.closed
    # Both ran on the same single executor thread.
    assert len(set(writer.write_threads + writer.close_threads)) == 1
    await executor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_without_run_is_noop():
    """shutdown() on a never-used executor is a safe no-op."""
    executor = _WriteExecutor()
    await executor.shutdown()  # must not raise


@pytest.mark.asyncio
async def test_run_exception_propagates_to_caller():
    """An exception raised on the worker thread propagates through run()."""
    executor = _WriteExecutor()

    def boom() -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        await executor.run(boom)
    await executor.shutdown()


@pytest.mark.asyncio
async def test_executor_is_restartable_after_shutdown():
    """A fresh run() after shutdown() creates a fresh executor and works."""
    executor = _WriteExecutor()
    writer = SlowWriter(delay=0.01)

    await executor.run(lambda: writer.write("first"))
    await executor.shutdown()

    # A second run() after shutdown must re-create the executor and work.
    await executor.run(lambda: writer.write("second"))
    await executor.shutdown()

    assert writer.writes == ["first", "second"]
