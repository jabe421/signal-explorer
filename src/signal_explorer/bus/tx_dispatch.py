"""Bounded serial lanes for native handles; no driver work in UI callers."""
from collections import deque
from concurrent.futures import Future
from threading import Condition, Thread
import errno
import can


class TxFailure(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def send_failure(exc, interface):
    if isinstance(exc, TxFailure):
        return exc
    code = getattr(exc, 'error_code', None)
    # Only explicit rejection before acceptance is retryable. In particular,
    # ACK/write timeouts may follow transmission and must not be replayed.
    queue_full = ((interface == 'vector' and code == 11) or
                  (interface == 'kvaser' and code == -13) or
                  (isinstance(exc, BlockingIOError) and exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK)
                   and not getattr(exc, 'characters_written', 0)))
    text = str(exc).lower()
    if interface == 'pcan' and ('transmit buffer' in text and 'full' in text):
        queue_full = True
    if isinstance(exc, can.CanTimeoutError):
        queue_full = False
    return TxFailure(str(exc), retryable=queue_full)


class _Lane:
    def __init__(self, capacity=512):
        self.condition = Condition()
        self.queue = deque()
        self.closed = False
        self.capacity = capacity
        self.thread = Thread(target=self.run, name='SignalExplorer-TX', daemon=False)
        self.thread.start()

    def submit(self, operation):
        future = Future()
        item = (future, operation)
        with self.condition:
            if self.closed:
                future.set_exception(TxFailure('CAN session is closing.'))
            elif len(self.queue) >= self.capacity:
                future.set_exception(TxFailure('Application TX queue is full; frame was not submitted.', True))
            else:
                self.queue.append(item)
                self.condition.notify()
        def cancelled(done):
            if done.cancelled():
                with self.condition:
                    try: self.queue.remove(item)
                    except ValueError: pass
        future.add_done_callback(cancelled)
        return future

    def run(self):
        while True:
            with self.condition:
                while not self.queue and not self.closed:
                    self.condition.wait()
                if not self.queue:
                    return
                future, operation = self.queue.popleft()
            if not future.set_running_or_notify_cancel():
                continue
            try: future.set_result(operation())
            except Exception as exc: future.set_exception(exc)

    def stop(self):
        with self.condition:
            self.closed = True
            queued = list(self.queue)
            self.queue.clear()
            self.condition.notify_all()
        for future, _ in queued:
            future.cancel()


class TxDispatcher:
    def __init__(self, channels):
        # A combined Vector/Intrepid port is one handle. ChannelMux exposes
        # independent handles, which may make progress concurrently.
        self.lanes = {str(key): _Lane() for key in channels}

    def submit(self, key, operation):
        lane = self.lanes.get(str(key))
        if lane is None:
            future = Future(); future.set_exception(TxFailure('TX channel is unavailable.'))
            return future
        return lane.submit(operation)

    def stop(self):
        for lane in self.lanes.values(): lane.stop()

    def join(self):
        for lane in self.lanes.values(): lane.thread.join()
