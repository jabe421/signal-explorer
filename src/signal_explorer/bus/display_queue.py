"""Bounded handoff to the monitor; recording and script mailboxes are independent."""
from collections import deque
from threading import Lock


class DisplayQueue:
    def __init__(self, capacity=20_000):
        if capacity < 1:
            raise ValueError('Display queue capacity must be positive.')
        self.capacity = capacity
        self._lock = Lock()
        self._frames = deque()
        self._epoch = 0
        self._dropped = 0

    @property
    def epoch(self):
        with self._lock:
            return self._epoch

    @property
    def dropped(self):
        with self._lock:
            return self._dropped

    def advance_epoch(self):
        # Pending frames still reach legacy display observers. A resumed/cleared
        # monitor sees only frames captured after its new boundary.
        with self._lock:
            self._epoch += 1

    def put(self, frame, epoch, legacy=True):
        with self._lock:
            if len(self._frames) == self.capacity:
                self._frames.popleft()
                self._dropped += 1
            self._frames.append((epoch, frame, legacy))

    def drain(self, limit):
        with self._lock:
            frames = [self._frames.popleft() for _ in range(min(limit, len(self._frames)))]
            return self._epoch, frames

    def reset(self):
        with self._lock:
            self._frames.clear()
            self._epoch += 1
            self._dropped = 0
