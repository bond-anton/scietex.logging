"""Tests for the JsonFormatter structured-logging formatter."""

import json
import logging
import sys

from scietex.logging.json_formatter import JsonFormatter


def _make_record(
    message: str = "hello",
    level: int = logging.INFO,
    exc_info=None,
) -> logging.LogRecord:
    return logging.LogRecord(
        name="TestLogger",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=message,
        args=None,
        exc_info=exc_info,
    )


def test_format_emits_single_line_json():
    """format() returns one JSON object per line with the core keys."""
    record = _make_record("hello world")
    out = JsonFormatter().format(record)

    assert "\n" not in out  # one line per record
    parsed = json.loads(out)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "TestLogger"
    assert "timestamp" in parsed


def test_timestamp_is_iso8601_utc():
    """The timestamp is ISO-8601 with a UTC offset."""
    record = _make_record()
    parsed = json.loads(JsonFormatter().format(record))
    # ISO-8601 with +00:00 offset (not naive, not Z-only).
    assert parsed["timestamp"].endswith("+00:00")


def test_format_flattens_user_extras_only():
    """User-added extra fields are flattened; stdlib attrs are not."""
    record = _make_record()
    record.user_id = 42
    record.request_id = "abc-123"
    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["user_id"] == 42
    assert parsed["request_id"] == "abc-123"
    # Stdlib LogRecord internals must not leak into the JSON line.
    assert "msg" not in parsed
    assert "args" not in parsed
    assert "levelno" not in parsed
    assert "pathname" not in parsed
    assert "thread" not in parsed


def test_format_includes_exception_only_when_exc_info():
    """The exception key appears only when exc_info is set, as a traceback string."""
    plain = json.loads(JsonFormatter().format(_make_record()))
    assert "exception" not in plain

    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", level=logging.ERROR, exc_info=sys.exc_info())
    out = JsonFormatter().format(record)
    parsed = json.loads(out)

    assert "exception" in parsed
    assert "boom" in parsed["exception"]
    assert "Traceback" in parsed["exception"]
    # The traceback spans multiple lines, but json.dumps escapes the newlines so
    # the record remains a single line in the NDJSON stream.
    assert "\n" not in out


def test_format_does_not_mutate_shared_record():
    """format() copies the record, so the caller's record is never mutated."""
    record = _make_record()
    original = dict(record.__dict__)
    JsonFormatter().format(record)
    assert dict(record.__dict__) == original


def test_format_survives_non_serializable_extra():
    """A non-serializable extra value degrades to its repr, never raising."""
    record = _make_record()
    record.obj = object()
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["obj"].startswith("<object")


def test_format_renders_message_with_args():
    """The message is the rendered (%-formatted) message, not the raw msg."""
    record = logging.LogRecord(
        name="TestLogger",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="value is %d",
        args=(42,),
        exc_info=None,
    )
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["message"] == "value is 42"
