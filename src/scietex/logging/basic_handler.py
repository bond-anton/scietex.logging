"""
Asynchronous base handler for non-blocking logging in Python applications.
Provides AsyncBaseHandler class.
"""

from typing import Any, Union, Coroutine
import sys
import logging
import asyncio

from .formatter import NTSFormatter


class AsyncBaseHandler(logging.Handler):  # type: ignore
    """
    Asynchronous base handler for non-blocking logging in Python applications.

    Overview:
        This handler serves as a base class for creating asynchronous logging handlers
        that process log records without blocking the main application flow.
        By default, `AsyncBaseHandler` provides a console logging backend that
        outputs log messages to the standard output. This backend can be disabled
        if desired, and additional backends (such as Redis) can be added in
        subclasses.

    Attributes:
        log_queues (dict): A dictionary of asyncio.Queue objects for each logging backend.
        logging_accept_event (asyncio.Event): Event to signal when the handler can accept new logs.
        logging_running_event (asyncio.Event): Event to signal when logging is active.
        log_workers (list): List of worker coroutine functions for processing log messages.
        log_workers_tasks (list): List of asyncio tasks for each worker, created in `start_logging`.
        log_queue_put_tasks (list): List of asyncio tasks for queue put operations.

    Methods:
        start_logging():
            Starts all worker tasks to process log records asynchronously.

        emit(record):
            Queues a log record for each backend if logging is active.

        stop_logging():
            Stops logging by clearing the event and waiting for all queues to complete.

        _console_worker():
            Worker coroutine to process console log records from the queue.
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        service_name: Union[str, None] = None,
        worker_id: Union[int, None] = None,
        **kwargs,
    ) -> None:
        """
        Initialize the asynchronous base logging handler.

        Args:
            service_name (str, optional): Name of the service for log identification.
                Defaults to "Service".
            worker_id (int, optional): Identifier for the worker instance. Defaults to 1.
            kwargs (dict): Additional arguments, including `stdout_enable`
                to enable/disable console logging.

        Attributes:
            stdout_enable (bool): Flag to enable console logging (defaults to True).
        """
        super().__init__()
        self.stdout_enable: bool = kwargs.get("stdout_enable", True)
        if worker_id is None:
            worker_id = 1
        if service_name is None:
            service_name = "Service"
        self.formatter = NTSFormatter(service_name=service_name, worker_id=worker_id)
        self.logging_accept_event = asyncio.Event()  # Indicates if logging is running
        self.logging_running_event = asyncio.Event()  # Indicates if logging is running

        self.log_queues: dict[str, asyncio.Queue[logging.LogRecord]] = {}  # type: ignore
        self.log_workers: list[Coroutine[Any, Any, None]] = []
        if self.stdout_enable:
            self.log_queues["console"] = asyncio.Queue()  # Queue for console logs
            self.log_workers.append(self._console_logging_worker())

        self.log_workers_tasks: list[asyncio.Task[None]] = []
        self.log_queue_put_tasks: list[asyncio.Task[None]] = []

    async def start_logging(self) -> None:
        """
        Start all logging workers asynchronously.

        Sets the `logging_accept_event` to allow the `emit` method to accept logs.
        Sets the `logging_running_event` to signal that logging has started and creates
        tasks for each worker in `self.log_workers`, allowing them to run concurrently.

        Returns:
            None
        """
        self.logging_accept_event.set()  # Set the event to indicate logs are accepted
        self.logging_running_event.set()  # Set the event to indicate logging is active
        self.log_workers_tasks = [
            asyncio.create_task(worker) for worker in self.log_workers
        ]

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore
        """
        Queue a log record for each backend when logging is active.

        Called by the logger to handle each log record. If the logging accept event
        is set, queues the record in the queues. Each backend can have
        a unique queue, allowing separate handling in different workers.

        Args:
            record (logging.LogRecord): The log record to be processed.

        Returns:
            None
        """
        if self.logging_accept_event.is_set():
            # Schedule an asynchronous task to put the message in each queue
            for _, queue in self.log_queues.items():
                try:
                    # Use asyncio.create_task to handle each put asynchronously
                    queue_put_task = asyncio.create_task(queue.put(record))
                    self.log_queue_put_tasks.append(
                        queue_put_task
                    )  # Track the put task
                    # Cleanup completed tasks from the list to prevent memory buildup
                    self.log_queue_put_tasks = [
                        task for task in self.log_queue_put_tasks if not task.done()
                    ]
                except asyncio.QueueFull:
                    # Queue is full; could log an error or drop the message based on policy
                    pass

                except asyncio.InvalidStateError:
                    # Happens if the event loop is in an invalid state for this operation
                    pass
                # pylint: disable=broad-exception-caught
                except Exception:
                    # Log unexpected errors for visibility without halting other tasks
                    pass
        else:
            pass

    async def stop_logging(self, timeout: float = 5.0) -> None:
        """
        Stop logging and ensure all queues are processed.

        Clears the logging event, stopping further log entries from being queued.
        Waits for all items in the console queue to be processed. Additional queues
        for other backends should be added here if extended.

        Args:
            timeout (float): Timeout for the queues and tasks to finish, defaults to 5s.

        Returns:
            None
        """

        # Stop accepting new log records
        self.logging_accept_event.clear()

        # Wait for all pending put tasks to complete
        if self.log_queue_put_tasks:
            await asyncio.gather(*self.log_queue_put_tasks)
            self.log_queue_put_tasks = [
                task for task in self.log_queue_put_tasks if not task.done()
            ]

        # Signal workers to stop processing
        self.logging_running_event.clear()

        # Process each worker's queue except the console queue
        for name, queue in self.log_queues.items():
            if name == "console":
                continue  # Skip the console queue for now
            try:
                # Attempt to wait for the queue to join with a timeout
                await asyncio.wait_for(queue.join(), timeout=timeout)
                # Create an INFO LogRecord for successful completion
                if self.stdout_enable:
                    log_record = logging.LogRecord(  # type: ignore
                        name=f"{name.capitalize()}Logger",
                        level=logging.INFO,  # type: ignore
                        pathname=__file__,
                        lineno=0,
                        msg=f"{name.capitalize()} Logger has completed processing its queue.",
                        args=None,
                        exc_info=None,
                    )
                    await self.log_queues["console"].put(log_record)
            except asyncio.TimeoutError:
                # Create an ERROR LogRecord for timeout
                if self.stdout_enable:
                    log_record = logging.LogRecord(  # type: ignore
                        name=f"{name.capitalize()}Logger",
                        level=logging.ERROR,  # type: ignore
                        pathname=__file__,
                        lineno=0,
                        msg=f"Timeout while waiting for {name} logger to complete its queue.",
                        args=None,
                        exc_info=None,
                    )
                    await self.log_queues["console"].put(log_record)
            # pylint: disable=broad-exception-caught
            except Exception as e:
                # Create an ERROR LogRecord for other exceptions
                if self.stdout_enable:
                    log_record = logging.LogRecord(  # type: ignore
                        name=f"{name.capitalize()}Logger",
                        level=logging.ERROR,  # type: ignore
                        pathname=__file__,
                        lineno=0,
                        msg=f"Error while waiting for {name} Logger: {e}",
                        args=None,
                        exc_info=None,
                    )
                    await self.log_queues["console"].put(log_record)

        # Process the console queue last
        if self.stdout_enable:
            try:
                await asyncio.wait_for(
                    self.log_queues["console"].join(), timeout=timeout
                )
            except asyncio.TimeoutError:
                pass
            # pylint: disable=broad-exception-caught
            except Exception:
                pass
        # Wait for all worker tasks to complete
        if self.log_workers_tasks:
            await asyncio.gather(*self.log_workers_tasks)
        self.close()

    async def _console_logging_worker(self) -> None:
        """
        Asynchronous worker to handle console logging.

        Retrieves log records from the console queue, formatting and outputting
        them to the standard output. Continues running as long as logging is active
        or there are records in the queue.

        Returns:
            None
        """
        while (
            self.logging_running_event.is_set()
            or not self.log_queues["console"].empty()
        ):
            try:
                record = await asyncio.wait_for(self.log_queues["console"].get(), 1)
                sys.stdout.write(self.formatter.format(record) + "\n")
                sys.stdout.flush()
                self.log_queues["console"].task_done()
            except asyncio.TimeoutError:
                pass
