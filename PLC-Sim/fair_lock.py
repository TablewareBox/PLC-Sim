"""FIFO reentrant context lock for short durable facility transactions.

The model clock must not reacquire the environment lock ahead of public or
controller requests that already wait for it. This is an in-process scheduling
primitive, not a persistent execution authority or a wall-clock deadline.
"""
from collections import deque
import threading


class FairRLock:
    """The context-manager subset needed by the facility, with FIFO handoff."""

    def __init__(self):
        self._condition = threading.Condition(threading.Lock())
        self._owner = None
        self._depth = 0
        self._waiters = deque()

    def __enter__(self):
        owner = threading.get_ident()
        with self._condition:
            ticket = object()
            previous_depth = self._depth if self._owner == owner else None
            try:
                if previous_depth is not None:
                    self._depth = previous_depth + 1
                    return self
                self._waiters.append(ticket)
                while self._owner is not None or self._waiters[0] is not ticket:
                    self._condition.wait()
                self._waiters.popleft()
                self._owner = owner
                self._depth = 1
                return self
            except BaseException:
                # An interrupted wait cannot leave a dead ticket at the head.
                if ticket in self._waiters:
                    self._waiters.remove(ticket)
                if previous_depth is not None:
                    self._depth = previous_depth
                elif self._owner == owner:
                    self._owner = None
                    self._depth = 0
                self._condition.notify_all()
                raise

    def __exit__(self, _exc_type, _exc_value, _traceback):
        with self._condition:
            if self._owner != threading.get_ident() or self._depth <= 0:
                raise RuntimeError('fair_lock_not_owned')
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()
        return False
