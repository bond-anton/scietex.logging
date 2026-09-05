# P1 Worker-Failure Correctness Fixes (AR-013, AR-014, AR-015) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three Priority-1 broker-worker correctness defects — false drain TIMEOUT from `task_done()` poisoning (AR-013), unbounded shutdown hang on a down broker (AR-014), and no reconnect after send-time connection loss (AR-015).

**Architecture:** All three defects live in the broker worker's connect/send/drain error paths (`message_broker_handler.py`) and the shared shutdown gather (`async_logging_handler.py`). The fixes are: (1) acknowledge every dequeued record unconditionally via `task_done()` in a `finally`; (2) bound the worker `gather` in `stop_logging` with `asyncio.wait_for` and cancel stragglers; (3) reset the client to a disconnected state on send failure so the next loop iteration re-enters `connect()`. AR-013 and AR-015 both touch the worker's send-failure path and are coordinated in one task; AR-014 touches `stop_logging` and is independent.

**Tech Stack:** Python >=3.10, asyncio, pytest-asyncio, ruff, ty.

**Spec:** `docs/reviews/architecture/2026-09-05.md` findings AR-013, AR-014, AR-015 (lines 127-198). The plan argues from that review; executors read both this plan and the review.

## Global Constraints

- Working tree must stay green after each task: `uv run pytest -q`, `uv run ruff check .`, `uv run ty check src/scietex/logging/`.
- Do NOT modify files outside the exact paths listed per task. AR-032 (disconnect-in-finally), AR-034 (send_message no-op precondition), AR-022 (backoff), and the retry/dead-letter option are **out of scope** — do not implement them here.
- Do not overload `task_done()` as a redelivery signal; `asyncio.Queue` has no such semantics. Ack the processing attempt; visibility comes from `_report_error` and drain results.
- Do not fight Valkey Glide's native reconnection. The reconnect-on-send-failure reset is a worker-level fallback that only fires when `send_message` actually raises.
- Match existing style: `const`-like `final`/plain assignments, early return, no emoji, no commented-out code, comments explain WHY not WHAT.
- Commit style follows repo history: `fix: ...`, `test: ...`, `docs: ...` conventional prefixes (see `git log --oneline`).
- Every public method/behavior change needs a test; no dead code.

---

## File Structure

Files touched across the three tasks:

| File | Responsibility | Change |
|---|---|---|
| `src/scietex/logging/message_broker_handler.py` | Broker worker + drain | AR-013: `task_done()` in `finally`; AR-015: reset client on send failure |
| `src/scietex/logging/async_logging_handler.py` | Shared machinery + shutdown | AR-014: bound worker gather with `wait_for` + cancel |
| `tests/test_message_broker_handler.py` | Broker worker unit tests | Update AR-013 test; add AR-015 reconnect test |
| `tests/test_restartable_lifecycle.py` | Restart lifecycle tests | Rework `test_restart_after_drain_timeout` (AR-013 model); add AR-014 shutdown-hang test |
| `docs/reviews/architecture/2026-09-05.md` | Review record | Append Resolution notes for AR-013/014/015 |
| `docs/architecture/lifecycle.md` | Lifecycle doc | Update shutdown step 4 to describe bounded gather + worker stop signal |

---

## Task 1: AR-013 — Ack every dequeued record via `task_done()` in a `finally`

**Files:**
- Modify: `src/scietex/logging/message_broker_handler.py:165-170`
- Test: `tests/test_message_broker_handler.py:98-120`
- Test: `tests/test_restartable_lifecycle.py:204-237`
- Docs: `docs/reviews/architecture/2026-09-05.md` (Resolution note)

**Interfaces:**
- Consumes: existing `_worker` loop structure (`message_broker_handler.py:141-174`), `_report_error(record, exc)`.
- Produces: worker acks every dequeued record exactly once, so `queue.join()` (drain, `:193`) completes after the last record is processed regardless of send success/failure. Drain reports `COMPLETED` when the queue empties, `TIMEOUT` only when records genuinely remain unprocessed past the window.

**Background (why):** The worker does `record = await queue.get()` (`:150`) then, on `send_message` failure, `_report_error(record, exc); continue` (`:167-169`) **without** `task_done()`. The record was already removed by `get()`, so `continue` neither retries nor preserves it — it is silently lost. The only effect of withholding `task_done()` is to leave `Queue._unfinished_tasks` elevated forever, so `queue.join()` (`:193`) can never complete and the drain always reports `DrainStatus.TIMEOUT` (`:195`) plus a misleading console timeout for every run with any failed delivery. Because the queue object persists across restarts, the counter stays poisoned for the handler's lifetime.

- [ ] **Step 1: Write the failing test (update the flawed AR-013 test)**

Replace `test_send_failure_surfaces_and_does_not_task_done` in `tests/test_message_broker_handler.py:98-120`. The new contract: a send failure is reported AND the record is acknowledged (processing attempt complete), so the queue can drain cleanly.

```python
@pytest.mark.asyncio
async def test_send_failure_surfaces_and_acks_record():
    """Send failures are reported and the record is acknowledged (processing attempt done)."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        stdout_enable=False,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler._send_error = RuntimeError("send failed")
    counting_queue = CountingQueue()
    handler.log_queues["broker"] = counting_queue

    await handler.start_logging()
    handler.emit(_make_record("hello"))

    await _wait_for(lambda: bool(errors))
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert handler.send_attempts  # send_message was attempted
    # The failed record is acknowledged so the queue can drain; the drop is
    # surfaced via the error channel, not by poisoning the drain counter.
    assert counting_queue.task_done_calls == 1
    assert counting_queue.empty()

    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_message_broker_handler.py::test_send_failure_surfaces_and_acks_record -v`
Expected: FAIL — `assert counting_queue.task_done_calls == 1` fails because current code calls `task_done()` 0 times on send failure.

- [ ] **Step 3: Implement the minimal fix**

In `src/scietex/logging/message_broker_handler.py`, restructure the send block (`:165-170`) so `task_done()` runs in a `finally`. The record is dequeued at `:150`; every path after dequeue must ack exactly once.

Current (`:165-170`):
```python
            try:
                await self.send_message(log_entry)
            except Exception as exc:
                self._report_error(record, exc)
                continue
            self.log_queues[self.queue_name].task_done()
```

New:
```python
            try:
                await self.send_message(log_entry)
            except Exception as exc:
                # The record was already dequeued by get(); ack the processing
                # attempt so queue.join() can complete. Visibility of the drop
                # comes from _report_error, not from withholding task_done().
                self._report_error(record, exc)
            finally:
                self.log_queues[self.queue_name].task_done()
```

Note: the `continue` is removed because the `finally` ack is the last statement of the loop body anyway; control falls through to the next `while` iteration naturally. Verify the loop still iterates correctly (the `while` condition re-checks `logging_running_event` / queue emptiness).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_message_broker_handler.py::test_send_failure_surfaces_and_acks_record -v`
Expected: PASS.

- [ ] **Step 5: Rework `test_restart_after_drain_timeout` (encodes the flawed model)**

In `tests/test_restartable_lifecycle.py:204-237`, the test relies on the OLD behavior where a failed send is NOT acked, so the drain `join()` blocks and times out while the worker keeps retrying. Under the new model, a failed send is acked immediately, so the drain `join()` completes as soon as the record is processed — the drain reports `COMPLETED`, not `TIMEOUT`, and `stop_logging` returns quickly. The test's premise ("broker-down drain timeout") no longer holds for a *send* failure.

Rework the test to assert the new, correct semantics: a send failure is acked, the drain completes (no false timeout), and a restart still recovers. The `FlakyBrokerHandler` (fails N times then succeeds) is still useful to prove the worker keeps processing subsequent records after a failure.

```python
@pytest.mark.asyncio
async def test_send_failure_acks_and_restart_recovers():
    """A send failure is acked (no false drain timeout); a restart recovers."""
    errors = []
    handler = FlakyBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler.failures_before_success = 2  # first two sends fail, then succeed

    await handler.start_logging()
    handler.emit(_make_record("flaky"))
    await _wait_for(lambda: handler.send_attempts >= 3)  # 2 failures + 1 success
    assert len(handler.sent) == 1
    assert handler.sent[0]["message"] == "flaky"
    assert len(errors) == 2

    # The failed sends were acked, so stop_logging drains cleanly and returns
    # promptly (no false TIMEOUT, no hang).
    await asyncio.wait_for(handler.stop_logging(timeout=0.5), timeout=10)
    assert handler.client is None

    # A fresh start schedules a fresh worker that reconnects and delivers.
    sent_before = len(handler.sent)
    await handler.start_logging()
    handler.emit(_make_record("recovered"))
    await _wait_for(lambda: len(handler.sent) == sent_before + 1)
    assert handler.sent[-1]["message"] == "recovered"
    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 6: Run the full broker + lifecycle test files**

Run: `uv run pytest tests/test_message_broker_handler.py tests/test_restartable_lifecycle.py -v`
Expected: PASS (all tests, including the reworked ones).

- [ ] **Step 7: Append Resolution note to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, after the AR-013 finding block (ends line 153), append a Resolution note following the AR-005/AR-007 convention in `2026-09-04.md` (a `**Resolution (AR-013):**` paragraph under the finding).

```markdown
**Resolution (AR-013):** Resolved. The broker worker now calls `task_done()` in a
`finally` after every `queue.get()`, acknowledging the *processing attempt*
regardless of send success or failure. A failed send is reported via
`_report_error` and the record is acked, so `queue.join()` completes once the
queue empties and the drain reports `COMPLETED`; `TIMEOUT` now only fires when
records genuinely remain unprocessed past the window. `task_done()` is not
overloaded as a redelivery signal. `test_send_failure_surfaces_and_does_not_task_done`
was renamed to `test_send_failure_surfaces_and_acks_record` and now asserts the
record is acked; `test_restart_after_drain_timeout` was reworked to
`test_send_failure_acks_and_restart_recovers`. Bounded retry / dead-letter for
failed sends remains an open follow-up (see Open Questions #2).
```

- [ ] **Step 8: Commit**

```bash
git add src/scietex/logging/message_broker_handler.py tests/test_message_broker_handler.py tests/test_restartable_lifecycle.py docs/reviews/architecture/2026-09-05.md
git commit -m "fix: ack dequeued records in broker worker finally (AR-013)"
```

---

## Task 2: AR-014 — Bound worker termination in `stop_logging`; give workers a stop signal

**Files:**
- Modify: `src/scietex/logging/async_logging_handler.py:283-290`
- Modify: `src/scietex/logging/message_broker_handler.py:141-148` (connect-retry loop checks a stop signal)
- Test: `tests/test_restartable_lifecycle.py` (new shutdown-hang test)
- Docs: `docs/reviews/architecture/2026-09-05.md` (Resolution note), `docs/architecture/lifecycle.md:73-77`

**Interfaces:**
- Consumes: `self.logging_running_event` (already cleared at `:284` before the gather), `self.log_workers_tasks`.
- Produces: `stop_logging` returns within a bounded window even when a worker is stuck in a connect-retry loop; workers observe the cleared running event and stop retrying. The worker's connect-retry loop (`message_broker_handler.py:141-148`) must exit when `logging_running_event` is cleared, not retry forever.

**Background (why):** `stop_logging` applies `timeout` only to the drain hooks (`queue.join()` in `wait_for`, `:280-281`), never to the final `await asyncio.gather(*self.log_workers_tasks)` (`:289`). The broker worker's connect-retry loop (`message_broker_handler.py:141-148`) never terminates when the broker is unreachable: `_report_error(None, exc); await asyncio.sleep(1.0); continue` indefinitely. If Redis/Valkey is down at shutdown, the drain `join()` times out (correctly) but the worker is left in an infinite connect-retry loop and `gather` blocks indefinitely — a real hang because `redis.Redis`/`GlideClient.create` have no bounded socket timeout by default.

**Decision (recommended):** Combine both review options: (a) wrap the final gather in `asyncio.wait_for(timeout)` and cancel remaining tasks on timeout, AND (b) make the worker's connect-retry loop exit when `logging_running_event` is cleared (the running event is already the natural "shutdown underway" signal — it is cleared at `:284` before the gather). This gives a clean two-layer bound: workers stop retrying promptly once the event clears, and the `wait_for` is a hard backstop for any worker stuck mid-connect/send. This preserves AR-005 restartability: cancelled tasks are simply forgotten (`log_workers_tasks = []`), and the next `start_logging` schedules fresh tasks from the factories.

- [ ] **Step 1: Write the failing test (shutdown does not hang on a down broker)**

Add to `tests/test_restartable_lifecycle.py`. Use a broker whose `connect()` always fails (never succeeds), so the worker is stuck in the connect-retry loop. Assert `stop_logging` returns within a bounded time (not the full indefinite hang) and that the worker task is cancelled/terminated.

```python
class NeverConnectsBrokerHandler(AsyncBrokerHandler):
    """Broker whose connect() always fails, keeping the worker in its retry loop."""

    def __init__(self, *args, **kwargs):
        self.connect_attempts = 0
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        raise ConnectionError("broker unreachable")

    async def disconnect(self) -> None:
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        raise ConnectionError("no client")


@pytest.mark.asyncio
async def test_stop_logging_does_not_hang_on_down_broker():
    """stop_logging returns promptly even when the broker worker never connects."""
    errors = []
    handler = NeverConnectsBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        error_handler=lambda record, exc: errors.append(exc),
    )

    await handler.start_logging()
    handler.emit(_make_record("hello"))
    await _wait_for(lambda: handler.connect_attempts >= 2)  # worker is retrying

    # stop_logging must return within a bounded window, not hang on the
    # connect-retry loop. The worker observes the cleared running event and
    # stops retrying; the wait_for is a hard backstop.
    await asyncio.wait_for(handler.stop_logging(timeout=0.5), timeout=5)
    assert handler.log_workers_tasks == []
    assert not handler.logging_running_event.is_set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_restartable_lifecycle.py::test_stop_logging_does_not_hang_on_down_broker -v`
Expected: FAIL — `asyncio.wait_for(..., timeout=5)` raises `TimeoutError` because the worker never exits its connect-retry loop and the gather blocks forever.

- [ ] **Step 3: Make the worker's connect-retry loop exit on the running-event clear**

In `src/scietex/logging/message_broker_handler.py`, the connect-retry loop is the `while` body's `if self.client is None:` block (`:142-148`). The `while` condition (`:141`) already re-checks `logging_running_event` each iteration, but the retry path does `await asyncio.sleep(1.0); continue` (`:147-148`) which re-enters the `while` check — so it DOES exit once the event clears. The problem is only that the event is cleared AFTER the drain (`:284`), and the drain itself can take up to `timeout`. So the worker's retry loop is not the unbounded part once the event clears.

The real unbounded part is the `gather` at `:289` if a worker is mid-`connect()` (blocked on a socket with no timeout) when the event clears — it cannot observe the event until `connect()` returns. So the fix is the `wait_for` backstop in `stop_logging`. No change is strictly needed to the retry loop itself for the event-clear case, but verify the loop exits promptly once the event clears (it does, via the `while` re-check). Leave `message_broker_handler.py` unchanged in this task unless the test reveals otherwise.

- [ ] **Step 4: Bound the gather in `stop_logging`**

In `src/scietex/logging/async_logging_handler.py:286-290`, wrap the gather in `asyncio.wait_for` and cancel stragglers on timeout.

Current (`:286-290`):
```python
        # Wait for all worker tasks to complete, then forget them so a later stop
        # does not re-gather already-finished tasks.
        if self.log_workers_tasks:
            await asyncio.gather(*self.log_workers_tasks)
        self.log_workers_tasks = []
```

New:
```python
        # Wait for all worker tasks to complete, then forget them so a later stop
        # does not re-gather already-finished tasks. The gather is bounded by the
        # same timeout as the drains: a worker stuck mid-connect/send (e.g. a down
        # broker with no socket timeout) must not hang shutdown forever. On
        # timeout, cancel the stragglers so the handler is restartable.
        if self.log_workers_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.log_workers_tasks), timeout=timeout
                )
            except asyncio.TimeoutError:
                for task in self.log_workers_tasks:
                    task.cancel()
                await asyncio.gather(*self.log_workers_tasks, return_exceptions=True)
        self.log_workers_tasks = []
```

Note: `asyncio.CancelledError` propagates out of the cancelled tasks; `return_exceptions=True` on the second gather swallows it so `stop_logging` does not raise. The worker's `disconnect()` cleanup on cancellation is AR-032 (out of scope) — this change does not make it worse; it just ensures the task is cancelled rather than leaked.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_restartable_lifecycle.py::test_stop_logging_does_not_hang_on_down_broker -v`
Expected: PASS — `stop_logging` returns within the bounded window.

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `uv run pytest -q`
Expected: PASS (67+ tests). Watch for any test that relied on the unbounded gather (e.g. a slow worker that needed more than `timeout` to finish — none should, since the drain already bounds the window).

- [ ] **Step 7: Append Resolution note to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, after the AR-014 finding block (ends line 174):

```markdown
**Resolution (AR-014):** Resolved. `stop_logging` now bounds the final worker
`gather` with `asyncio.wait_for(timeout)` (the same timeout as the drains) and,
on timeout, cancels the remaining worker tasks and re-gathers with
`return_exceptions=True` so shutdown returns promptly instead of hanging on a
worker stuck mid-connect/send against a down broker. The worker's connect-retry
loop already exits when `logging_running_event` is cleared (the `while`
condition re-checks it each iteration), so the `wait_for` is a hard backstop for
workers blocked inside `connect()`/`send_message()` that cannot observe the
event until the call returns. Restartability (AR-005) is preserved: cancelled
tasks are forgotten and the next `start_logging` schedules fresh tasks from the
factories. Documented in `docs/architecture/lifecycle.md`.
```

- [ ] **Step 8: Update `docs/architecture/lifecycle.md` shutdown step 4**

In `docs/architecture/lifecycle.md:73-77`, update step 4 to describe the bounded gather:

```markdown
4. Gather worker tasks, bounded by the same `timeout` as the drains
   (`asyncio.wait_for`). Workers exit their loop once `running_event` clears
   and, for broker workers, call `disconnect()`. If a worker is stuck
   mid-connect/send past the timeout (e.g. a down broker with no socket
   timeout), it is cancelled so shutdown never hangs.
```

- [ ] **Step 9: Commit**

```bash
git add src/scietex/logging/async_logging_handler.py tests/test_restartable_lifecycle.py docs/reviews/architecture/2026-09-05.md docs/architecture/lifecycle.md
git commit -m "fix: bound worker gather in stop_logging to prevent shutdown hang (AR-014)"
```

---

## Task 3: AR-015 — Reconnect after send-time connection loss

**Files:**
- Modify: `src/scietex/logging/message_broker_handler.py:165-170` (send-failure path resets client)
- Test: `tests/test_message_broker_handler.py` (new reconnect test)
- Docs: `docs/reviews/architecture/2026-09-05.md` (Resolution note)

**Interfaces:**
- Consumes: `self.client` (set by `connect()`, reset by `disconnect()`), the send-failure `except` block from Task 1.
- Produces: when `send_message` raises, the worker transitions the client to a disconnected state so the next loop iteration re-enters `connect()` and reconnects before the next send. This is coordinated with Task 1's `finally` ack (both touch the same block).

**Background (why):** The worker enters the connect path only when `self.client is None` (`message_broker_handler.py:142`); `self.client` is set to `None` only in `disconnect()` (`redis_handler.py:115`, `valkey_handler.py:115`), reached only at worker loop exit (`:171-174`). If the connection drops mid-stream, `send_message` raises, the worker reports and continues, but `client` is still the dead object — the next iteration skips reconnection and calls `xadd` on a dead client forever. Each queued record fails once and is dropped (AR-013), so an outage means total log loss until the handler is restarted. redis-py's pool does not auto-recover by default (`health_check_interval=0`); Valkey Glide has built-in reconnection, so the two adapters behave asymmetrically.

**Decision (recommended):** Option (a) — the worker calls `self.disconnect()` in the send-failure `except` path before continuing, so the next loop iteration re-enters `connect()`. This is the minimal, adapter-agnostic mechanism: it reuses the existing `disconnect()` contract (which resets `self.client = None` in both adapters) and does not require a new typed exception. It does not fight Valkey Glide's native reconnection — if Glide already recovered internally, `send_message` won't raise and this path won't fire; if it does raise, resetting the client is the correct fallback. Note: `disconnect()` may itself raise (e.g. closing a dead socket); guard it so a disconnect failure does not mask the original send error or skip the ack.

- [ ] **Step 1: Write the failing test (reconnect after send failure)**

Add to `tests/test_message_broker_handler.py`. Extend `FakeBrokerHandler` to simulate a connection that drops after the first successful send: the first send succeeds, then the client is "dead" and subsequent sends raise until `connect()` is called again. Track `disconnect` calls.

Modify `FakeBrokerHandler` (`:44-69`) to add a `disconnect_calls` counter and a mode where sends fail until reconnect:

```python
class FakeBrokerHandler(AsyncBrokerHandler):
    """Concrete broker handler recording connect/send activity for tests."""

    def __init__(self, *args, **kwargs):
        self.sent: list[dict[str, str]] = []
        self.send_attempts: list[dict[str, str]] = []
        self.connect_attempts = 0
        self.connect_failures = 0
        self.disconnect_calls = 0
        self._send_error: Exception | None = None
        self._fail_sends_until_reconnect = False
        super().__init__(*args, **kwargs)

    async def connect(self) -> None:
        self.connect_attempts += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("connect failed")
        self.client = object()
        self._fail_sends_until_reconnect = False  # a fresh connect clears the dead state

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.client = None

    async def send_message(self, record: dict[str, str]) -> None:
        self.send_attempts.append(record)
        if self._send_error is not None:
            raise self._send_error
        if self._fail_sends_until_reconnect:
            # Simulate a connection that dropped mid-stream: the client object is
            # still set but dead, so sends raise until the worker reconnects.
            raise ConnectionError("connection lost")
        self.sent.append(record)
```

New test:

```python
@pytest.mark.asyncio
async def test_reconnect_after_send_time_connection_loss():
    """A send-time connection loss resets the client so the next iteration reconnects."""
    errors = []
    handler = FakeBrokerHandler(
        queue_name="broker",
        service_name="TestService",
        worker_id=1,
        stdout_enable=False,
        error_handler=lambda record, exc: errors.append(exc),
    )
    handler._fail_sends_until_reconnect = True  # first send fails (dead client)

    await handler.start_logging()
    handler.emit(_make_record("first"))

    # The first send fails; the worker must reset the client and reconnect, then
    # deliver the next record on the fresh connection.
    await _wait_for(lambda: len(handler.sent) == 1)
    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert handler.connect_attempts == 2  # initial connect + reconnect after loss
    assert handler.disconnect_calls == 1  # the dead client was torn down
    assert handler.sent[0]["message"] == "first"

    await handler.stop_logging(timeout=0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_message_broker_handler.py::test_reconnect_after_send_time_connection_loss -v`
Expected: FAIL — `_wait_for(lambda: len(handler.sent) == 1)` times out because the worker never reconnects (client stays the dead object) and every send fails forever.

- [ ] **Step 3: Implement the fix (reset client on send failure)**

In `src/scietex/logging/message_broker_handler.py`, extend the send-failure `except` block from Task 1 to tear down the dead client so the next loop iteration re-enters `connect()`. This is coordinated with the Task 1 `finally` ack.

Current (after Task 1):
```python
            try:
                await self.send_message(log_entry)
            except Exception as exc:
                # The record was already dequeued by get(); ack the processing
                # attempt so queue.join() can complete. Visibility of the drop
                # comes from _report_error, not from withholding task_done().
                self._report_error(record, exc)
            finally:
                self.log_queues[self.queue_name].task_done()
```

New:
```python
            try:
                await self.send_message(log_entry)
            except Exception as exc:
                # The record was already dequeued by get(); ack the processing
                # attempt so queue.join() can complete. Visibility of the drop
                # comes from _report_error, not from withholding task_done().
                self._report_error(record, exc)
                # A send failure may mean the connection dropped mid-stream. Tear
                # down the client so the next loop iteration re-enters connect()
                # and reconnects before the next send; otherwise the dead client
                # is reused forever and every queued record is lost. A disconnect
                # failure must not mask the original send error.
                try:
                    await self.disconnect()
                except Exception:
                    self.client = None
            finally:
                self.log_queues[self.queue_name].task_done()
```

Note: `disconnect()` in both adapters resets `self.client = None` (`redis_handler.py:115`, `valkey_handler.py:115`). The inner `except` sets `self.client = None` directly as a fallback if `disconnect()` itself raises (e.g. closing a dead socket), guaranteeing the next iteration reconnects. This does not hit the AR-034 no-op path (`send_message` silently no-ops when client is None) because the worker reconnects before the next send.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_message_broker_handler.py::test_reconnect_after_send_time_connection_loss -v`
Expected: PASS.

- [ ] **Step 5: Run the full broker + lifecycle test files (coordination check)**

Run: `uv run pytest tests/test_message_broker_handler.py tests/test_restartable_lifecycle.py -v`
Expected: PASS. Confirm the Task 1 reworked tests still pass with the added `disconnect()` call in the send-failure path (the `FakeBrokerHandler.disconnect` now increments `disconnect_calls`; existing tests that assert `connect_attempts`/`client is None` must still hold).

- [ ] **Step 6: Append Resolution note to the review doc**

In `docs/reviews/architecture/2026-09-05.md`, after the AR-015 finding block (ends line 198):

```markdown
**Resolution (AR-015):** Resolved. The broker worker now calls `disconnect()` in
the send-failure path (with a `self.client = None` fallback if `disconnect()`
itself raises), so a send-time connection loss transitions the client to a
disconnected state and the next loop iteration re-enters `connect()` and
reconnects before the next send. This is adapter-agnostic and does not fight
Valkey Glide's native reconnection — if Glide already recovered internally,
`send_message` does not raise and this path does not fire. Coordinated with
AR-013 (the record is still acked in the `finally`). Added
`test_reconnect_after_send_time_connection_loss`. Bounded retry / dead-letter
for failed sends remains an open follow-up (see Open Questions #2).
```

- [ ] **Step 7: Commit**

```bash
git add src/scietex/logging/message_broker_handler.py tests/test_message_broker_handler.py docs/reviews/architecture/2026-09-05.md
git commit -m "fix: reconnect after send-time connection loss (AR-015)"
```

---

## Task 4: Final verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full verification command set**

Run:
```bash
uv run pytest -q
uv run ruff check .
uv run ty check src/scietex/logging/
```
Expected: all tests pass (67+), ruff clean, ty clean.

- [ ] **Step 2: Re-read the modified worker block end-to-end**

Read `src/scietex/logging/message_broker_handler.py:141-180` and confirm: the connect-retry loop exits when the running event clears; every dequeued record is acked exactly once in the `finally`; a send failure reports, disconnects (resetting the client), and falls through to the next iteration which reconnects. Confirm no leftover `continue`, no double `task_done()`, no commented-out code.

- [ ] **Step 3: Grep for broken callers**

Run: `rg "task_done|disconnect\(|log_workers_tasks" src/scietex/logging/ tests/`
Confirm no test or caller still encodes the old "no task_done on send failure" model and no caller assumes the unbounded gather.

---

## Ordering Dependencies

- **Task 1 (AR-013) first.** It is the one-line correctness fix and unblocks the drain-status story. Tasks 2 and 3 both build on the worker block that Task 1 restructures.
- **Task 2 (AR-014) is independent** of Task 1's worker change (it touches `stop_logging` in `async_logging_handler.py`), but doing it after Task 1 keeps the drain semantics settled first. It can be run in parallel with Task 1 if desired.
- **Task 3 (AR-015) must follow Task 1** because both edit the same send-failure block (`message_broker_handler.py:165-170`). Task 3's edit assumes Task 1's `finally` ack is in place. Do NOT run Task 3 before Task 1.
- **Task 4** is the final gate.

## Test Strategy

- **AR-013:** Update `test_send_failure_surfaces_and_does_not_task_done` → `test_send_failure_surfaces_and_acks_record` (asserts `task_done_calls == 1` and queue empty). Rework `test_restart_after_drain_timeout` → `test_send_failure_acks_and_restart_recovers` (asserts no false drain timeout, restart recovers).
- **AR-014:** New `test_stop_logging_does_not_hang_on_down_broker` using a `NeverConnectsBrokerHandler`; asserts `stop_logging` returns within a bounded window.
- **AR-015:** New `test_reconnect_after_send_time_connection_loss`; asserts the worker reconnects (connect_attempts == 2) and delivers after a send-time loss.
- All tests use the existing `_wait_for` predicate-polling helper (no fixed sleeps) and `FakeBrokerHandler`/`FlakyBrokerHandler` fakes — no live Redis/Valkey needed.

## Risks / Decisions Needing Sign-off

1. **AR-013 send-failure policy (Open Question #2):** This plan implements ack-and-drop (with `_report_error` visibility). Bounded re-queue / dead-letter is explicitly out of scope and flagged as a follow-up. If the user wants retry semantics, that is a larger behavior change and should be a separate plan.
2. **AR-014 cancellation semantics:** On gather timeout, workers are cancelled. A worker cancelled mid-`send_message` leaves the in-flight record dequeued and un-acked (the `finally` ack runs on `CancelledError`? — no: `CancelledError` is a `BaseException`, not caught by `except Exception`, but the `finally` DOES run on cancellation, so the ack still fires). Verify this in Task 2's test. Full disconnect-on-cancellation cleanup is AR-032 (out of scope); this change does not make it worse.
3. **AR-015 reconnect trigger:** This resets the client on ANY send failure, not just connectivity-class failures. A non-connectivity send error (e.g. a malformed record) would also trigger a reconnect — harmless (reconnect is cheap and idempotent) but slightly broader than strictly necessary. The typed-exception alternative (option b) is more precise but adds a new exception type and adapter changes; not chosen for the minimal P1 fix. Flag if the user prefers the typed approach.
4. **Valkey Glide asymmetry:** Glide's native reconnection means its `send_message` may not raise on a transient loss, so this path may fire less often for Valkey. That is the intended baseline (don't fight native reconnection).

## Handoff Plan

1. Execute Task 1 (AR-013): restructure `message_broker_handler.py:165-170` to ack in `finally`; update the two flawed tests; append Resolution note.
2. Execute Task 2 (AR-014): bound the gather at `async_logging_handler.py:288-290` with `wait_for` + cancel; add the shutdown-hang test; update lifecycle doc.
3. Execute Task 3 (AR-015): add `disconnect()` (with `self.client = None` fallback) to the send-failure path; add the reconnect test.
4. Execute Task 4: full verification command set.
- Risk: Task 3 must not run before Task 1 (same code block). Task 2's cancellation must not skip the `finally` ack.
- Test: `uv run pytest -q && uv run ruff check . && uv run ty check src/scietex/logging/` all green; the three new/reworked tests pass individually.
