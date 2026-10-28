import math
import threading
import time
from collections.abc import Callable

WINDOW_SECONDS = 60


class FixedWindowLimiter:
    """Allows `limit` requests per client in each one-minute window of `clock`.

    Only the current window's counts are kept, so memory is bounded by the
    clients seen in one minute.
    """

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._clock = clock
        self._lock = threading.Lock()
        self._window = -1
        self._counts: dict[str, int] = {}

    def hit(self, client: str) -> int | None:
        """Count a request. Returns None if allowed, else the seconds until the next window."""
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        with self._lock:
            if window != self._window:
                self._window = window
                self._counts = {}
            count = self._counts.get(client, 0)
            if count >= self._limit:
                return math.ceil(WINDOW_SECONDS - now % WINDOW_SECONDS)
            self._counts[client] = count + 1
            return None
