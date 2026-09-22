# Formatters

Formatters render log records to text. They apply to **console (stdout) and file
output only** — broker backends build their wire payload from the record
directly.

## ScietexFormatter

`ScietexFormatter` is the default formatter for `scietex.logging`. It provides:

- Logger name in logs via the `%(name)s` token (read from `record.name`)
- 3-letter log level abbreviations: `DBG`, `INF`, `WRN`, `ERR`, `CRT`
- ISO 8601 UTC timestamps by default

### Basic usage

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter()
```

With no arguments, `ScietexFormatter` uses the default format
`"%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"`, where `%(name)s`
renders the record's standard-library logger name.

### Custom format

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter(
    fmt="%(asctime)s - %(levelname)s - [%(name)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
```

### Date format

By default, timestamps use ISO 8601 format with UTC timezone. You can customize
this:

```python
from scietex.logging import ScietexFormatter

formatter = ScietexFormatter(datefmt="%Y-%m-%d %H:%M:%S")
```

### Color

`ScietexFormatter` accepts opt-in `theme=` and `color=` arguments. With no theme
the output is monochrome and byte-for-byte identical to a plain formatter. See
{doc}`themes`.

## JsonFormatter

For structured, machine-readable output, use `JsonFormatter` — a
`logging.Formatter` subclass that renders each record as a single-line JSON
object with keys `timestamp` (ISO-8601 UTC), `level`, `logger`, `message`, an
`exception` key (present only when the record carries `exc_info`), plus any
user-added `extra` fields flattened as top-level keys:

```python
from scietex.logging import AsyncFileHandler, JsonFormatter

handler = AsyncFileHandler("app.jsonl", formatter=JsonFormatter())
```

`JsonFormatter` copies the record before formatting (it never mutates the shared
record) and degrades non-serializable extra values to their `repr` rather than
raising.

## Custom formatters

You can use Python's standard `logging.Formatter` with custom formats:

```python
import logging
from scietex.logging import ConsoleHandler

formatter = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
)

handler = ConsoleHandler()
handler.setFormatter(formatter)
```

### Extending ScietexFormatter

You can extend the formatter to add custom fields:

```python
import copy
import logging
from scietex.logging import ScietexFormatter


class CustomFormatter(ScietexFormatter):
    def format(self, record):
        # Copy the record first so the shared record is not mutated in place.
        record = copy.copy(record)
        # Add custom fields
        record.custom_field = "value"
        return super().format(record)
```

For a runnable example of customizing `ScietexFormatter` and applying it with
`setFormatter`, see `examples/custom_formatter.py`.

(formatter-scope)=
## Formatter scope: console and file output only

A formatter — whether injected via the `formatter=` constructor keyword or
applied later with `setFormatter` — affects **console (stdout) and file output
only**. It is accepted only by `ConsoleHandler` and `AsyncFileHandler` (and its
rotation variants), which render text through it.

Broker handlers (`AsyncRedisHandler`, `AsyncValkeyHandler`, `AsyncMqttHandler`,
and any `AsyncBrokerHandler` subclass) do **not** accept a `formatter=` keyword
— passing one raises `TypeError`. They build their wire record from the log
record directly — its `name` field is the record's logger name (`record.name`) —
producing a fixed schema (`level`, `message`, `name`, `time`). That payload is
**invariant** under `setFormatter`, so the broker output is deterministic and
independent of any formatter you install.

Consequences to be aware of:

- On a broker handler, `setFormatter` (inherited from the stdlib
  `logging.Handler`) sets an unused attribute — it has **no visible effect** on
  broker output. Broker handlers do not register a console sink, and the broker
  builds its payload independently of the formatter. Console output requires
  adding a `ConsoleHandler` to the logger, with the formatter installed there.
- If you need to change what a broker backend sends, that is a property of the
  backend's `send_message` implementation, not of the formatter.

## Formatters must not mutate the record

The same `LogRecord` is fanned out to every backend queue by the bridge task on
the event-loop thread. The shipped `ScietexFormatter` copies the record before
formatting, so it never mutates the shared record. A **custom formatter must do
the same**: if it mutates the record in place (e.g. `record.custom_field = ...`),
the mutation leaks to the broker worker and to any other backend that later
reads the same record. Copy the record first (`import copy; record =
copy.copy(record)`) or avoid mutating it.
