"""A small retry helper.

Both LLM calls and network tool calls fail in transient ways: rate limits,
timeouts, the odd 5xx. This wraps a callable and retries it with backoff.
It is deliberately tiny. I did not want a dependency for something this
simple, and writing it myself means I can explain exactly what it does.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


class RetryError(Exception):
    """Raised when every attempt has been used up."""

    def __init__(self, attempts: int, last: BaseException):
        self.attempts = attempts
        self.last = last
        super().__init__(f"gave up after {attempts} attempts: {last!r}")


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    # Exceptions that are worth retrying. Anything else propagates straight
    # away, because retrying a bug in your own code just wastes time.
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with full jitter.

        attempt is 1-indexed. Jitter matters when several agents retry at
        once, otherwise they all wake up together and hammer the same
        endpoint in lockstep.
        """
        raw = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return random.uniform(0, raw)


def with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy,
    on_retry: Callable[[int, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run fn, retrying on the configured exceptions.

    on_retry is called before each sleep so the caller can log the attempt
    into the audit trail. sleep is injectable so tests do not actually wait.
    """

    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return fn()
        except policy.retry_on as exc:  # noqa: PERF203 - clarity over micro-perf
            last = exc
            if attempt == policy.attempts:
                break
            if on_retry is not None:
                on_retry(attempt, exc)
            sleep(policy.delay_for(attempt))
    assert last is not None
    raise RetryError(policy.attempts, last)
