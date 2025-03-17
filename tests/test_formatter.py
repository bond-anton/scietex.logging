"""Tests for NTSFormatter class and formatter module."""

import logging
from datetime import datetime, timezone
from scietex.logging.formatter import NTSFormatter, level_abbreviation


def test_level_abbreviation():
    """Test the level_abbreviation function for all logging levels."""
    assert level_abbreviation(logging.DEBUG) == "DBG"
    assert level_abbreviation(logging.INFO) == "INF"
    assert level_abbreviation(logging.WARNING) == "WRN"
    assert level_abbreviation(logging.ERROR) == "ERR"
    assert level_abbreviation(logging.CRITICAL) == "CRT"
    assert level_abbreviation(999) == "999"  # Unknown level


def test_nts_formatter_format_time():
    """Test the formatTime method to ensure it returns ISO format."""
    formatter = NTSFormatter(service_name="TestService", worker_id=42)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message", None, None)
    record.created = datetime(2024, 11, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    formatted_time = formatter.formatTime(record)
    expected_time = "2024-11-04T12:00:00+00:00"
    assert formatted_time == expected_time


def test_nts_formatter_format_with_worker_name():
    """Test the format method to include worker name and level abbreviation."""
    formatter = NTSFormatter(service_name="TestService", worker_id=42)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Test message", None, None)
    formatted_message = formatter.format(record)
    expected_message_part = " - INF - [TestService:42] - Test message"
    assert expected_message_part in formatted_message


def test_nts_formatter_format_with_debug_level():
    """Test the formatter with DEBUG level."""
    formatter = NTSFormatter(service_name="TestService", worker_id=42)
    record = logging.LogRecord(
        "test", logging.DEBUG, "", 0, "Debug message", None, None
    )
    formatted_message = formatter.format(record)
    assert " - DBG -" in formatted_message
