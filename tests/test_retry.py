"""Retry logic tests. No sleeping happens: sleep is injected as a no-op."""

from __future__ import annotations

import pytest

from strands.retry import RetryError, RetryPolicy, with_retry


def _noop(_seconds: float) -> None:
    pass


def test_succeeds_first_try():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert with_retry(fn, RetryPolicy(attempts=3), sleep=_noop) == "ok"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert with_retry(fn, RetryPolicy(attempts=3), sleep=_noop) == "ok"
    assert calls["n"] == 3


def test_gives_up_and_wraps_last_error():
    def fn():
        raise ValueError("always")

    with pytest.raises(RetryError) as info:
        with_retry(fn, RetryPolicy(attempts=2), sleep=_noop)
    assert info.value.attempts == 2
    assert isinstance(info.value.last, ValueError)


def test_on_retry_called_for_each_retry():
    seen: list[int] = []

    def fn():
        raise RuntimeError("boom")

    with pytest.raises(RetryError):
        with_retry(
            fn,
            RetryPolicy(attempts=3),
            on_retry=lambda attempt, exc: seen.append(attempt),
            sleep=_noop,
        )
    # called before attempts 1 and 2, not before the final failed attempt
    assert seen == [1, 2]


def test_non_retryable_exception_propagates_immediately():
    def fn():
        raise KeyError("nope")

    policy = RetryPolicy(attempts=5, retry_on=(ValueError,))
    with pytest.raises(KeyError):
        with_retry(fn, policy, sleep=_noop)


def test_backoff_is_bounded():
    policy = RetryPolicy(attempts=10, base_delay_s=1.0, max_delay_s=4.0)
    for attempt in range(1, 11):
        assert 0 <= policy.delay_for(attempt) <= 4.0
