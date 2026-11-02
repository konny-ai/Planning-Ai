"""Wall-clock limit for opt-in searches (#78 P16).

The candidate budget stays the preferred, deterministic bound. A time limit only
stops evaluating further candidates once the first (baseline) one is done; the best
verified result so far is returned and the result says the limit was reached.
Results under a time limit are not guaranteed to be repeatable.
"""
import time


class Deadline:
    def __init__(self, seconds: float | None, clock=time.monotonic):
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                                    or not 0 < seconds <= 86400):
            raise ValueError('time limit must be a number of seconds between 0 and 86400')
        self.clock = clock
        self.end = None if seconds is None else clock() + seconds

    def expired(self) -> bool:
        return self.end is not None and self.clock() >= self.end
