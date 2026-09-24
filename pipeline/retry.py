from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

import requests

P = ParamSpec("P")
T = TypeVar("T")


def retry_http(max_attempts: int = 4, base_delay_seconds: float = 0.5) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Retry transient HTTP failures with exponential backoff and full jitter."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(1, max_attempts + 1):
                try:
                    return function(*args, **kwargs)
                except requests.HTTPError as error:
                    status_code = error.response.status_code if error.response is not None else None
                    if status_code is not None and 400 <= status_code < 500 and status_code not in (408, 429):
                        raise
                    if attempt == max_attempts:
                        raise
                    delay = random.uniform(0, base_delay_seconds * (2 ** (attempt - 1)))
                    logging.warning("HTTP attempt %s/%s failed; retrying in %.2fs", attempt, max_attempts, delay)
                    time.sleep(delay)
                except Exception:
                    if attempt == max_attempts:
                        raise
                    delay = random.uniform(0, base_delay_seconds * (2 ** (attempt - 1)))
                    logging.warning("HTTP attempt %s/%s failed; retrying in %.2fs", attempt, max_attempts, delay)
                    time.sleep(delay)
            raise RuntimeError("unreachable")
        return wrapped
    return decorate
