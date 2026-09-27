import threading
import time
import sys

import pytest

from fair_lock import FairRLock


def wait_for_queue(lock, count):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with lock._condition:
            if len(lock._waiters) == count:
                return
        time.sleep(.001)
    raise AssertionError('waiter_not_queued')


def test_fifo_waiters_precede_continuously_reacquiring_clock():
    lock = FairRLock()
    order = []
    threads = []

    def read(index):
        with lock:
            order.append(index)

    def clock():
        for _ in range(1000):
            with lock:
                order.append('clock')

    with lock:
        for index in range(8):
            thread = threading.Thread(target=read, args=(index,))
            threads.append(thread)
            thread.start()
            wait_for_queue(lock, index + 1)
        writer = threading.Thread(target=clock)
        writer.start()
        wait_for_queue(lock, 9)
    for thread in threads + [writer]:
        thread.join(2)
        assert not thread.is_alive()
    assert order[:8] == list(range(8))
    assert order[8:] == ['clock'] * 1000


def test_reentrant_exception_only_releases_the_nested_depth():
    lock = FairRLock()
    acquired = threading.Event()
    with lock:
        with pytest.raises(ValueError, match='nested'):
            with lock:
                raise ValueError('nested')
        def waiter():
            with lock:
                acquired.set()
        thread = threading.Thread(target=waiter)
        thread.start()
        wait_for_queue(lock, 1)
        assert not acquired.is_set()
        with lock:
            assert lock._depth == 2 and not acquired.is_set()
    thread.join(2)
    assert acquired.is_set() and not thread.is_alive()
    with pytest.raises(ValueError, match='outer'):
        with lock:
            raise ValueError('outer')
    with lock:
        assert lock._depth == 1


def test_cancelled_middle_waiter_is_removed_and_notifies_successors(monkeypatch):
    lock = FairRLock()
    order = []
    cancelled = threading.Event()
    original_wait = lock._condition.wait

    class Cancelled(BaseException):
        pass

    def wait(*args, **kwargs):
        if threading.current_thread().name == 'cancel-this-waiter':
            raise Cancelled()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(lock._condition, 'wait', wait)

    def accepted(index):
        with lock:
            order.append(index)

    def cancellation():
        try:
            with lock:
                order.append('unexpected-cancelled-entry')
        except Cancelled:
            cancelled.set()

    with lock:
        first = threading.Thread(target=accepted, args=(1,)); first.start()
        wait_for_queue(lock, 1)
        aborting = threading.Thread(target=cancellation, name='cancel-this-waiter'); aborting.start()
        aborting.join(2)
        assert cancelled.is_set() and not aborting.is_alive()
        wait_for_queue(lock, 1)
        last = threading.Thread(target=accepted, args=(2,)); last.start()
        wait_for_queue(lock, 2)
    for thread in (first, last):
        thread.join(2)
        assert not thread.is_alive()
    assert order == [1, 2]
    with lock._condition:
        assert not lock._waiters and lock._owner is None and lock._depth == 0


def test_exit_from_another_thread_cannot_release_owner():
    lock = FairRLock()
    rejected = threading.Event()
    with lock:
        def intruder():
            try:
                lock.__exit__(None, None, None)
            except RuntimeError as exc:
                if str(exc) == 'fair_lock_not_owned':
                    rejected.set()
        thread = threading.Thread(target=intruder); thread.start(); thread.join(2)
        assert rejected.is_set() and lock._depth == 1


def test_metadata_entry_cancellation_preserves_existing_outer_owner():
    lock = FairRLock()
    original = lock._condition

    class Cancelled(BaseException):
        pass

    class InterruptedOnce:
        interrupted = False
        def __enter__(self):
            if not self.interrupted:
                self.interrupted = True
                raise Cancelled()
            return original.__enter__()
        def __exit__(self, *args):
            return original.__exit__(*args)
        def __getattr__(self, name):
            return getattr(original, name)

    with lock:
        lock._condition = InterruptedOnce()
        try:
            with pytest.raises(Cancelled):
                with lock:
                    raise AssertionError('unexpected_entry')
            assert lock._owner == threading.get_ident() and lock._depth == 1
        finally:
            lock._condition = original


@pytest.mark.parametrize('window', ['just_enqueued', 'reentered'])
def test_async_exception_window_leaves_no_orphan_ticket_or_depth(window):
    lock = FairRLock()

    class Cancelled(BaseException):
        pass

    def trace(frame, event, arg):
        if event == 'line' and frame.f_code is FairRLock.__enter__.__code__:
            if ((window == 'just_enqueued' and len(lock._waiters) == 1)
                    or (window == 'reentered' and lock._depth == 2)):
                raise Cancelled()
        return trace

    def interrupt():
        old_trace = sys.gettrace()
        try:
            sys.settrace(trace)
            with pytest.raises(Cancelled):
                with lock:
                    raise AssertionError('cancelled_acquisition_entered_body')
        finally:
            sys.settrace(old_trace)

    if window == 'reentered':
        with lock:
            interrupt()
            assert lock._owner == threading.get_ident() and lock._depth == 1
    else:
        interrupt()
    acquired = threading.Event()
    def successor():
        with lock:
            acquired.set()
    thread = threading.Thread(target=successor, daemon=True); thread.start(); thread.join(2)
    assert acquired.is_set() and not thread.is_alive()
    with lock._condition:
        assert not lock._waiters and lock._owner is None and lock._depth == 0
