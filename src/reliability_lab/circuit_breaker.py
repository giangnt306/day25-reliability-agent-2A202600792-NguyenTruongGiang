from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit is open and calls should fail fast."""


@dataclass(slots=True)
class CircuitBreaker:
    """Circuit breaker skeleton.

    TODO(student): Implement a production-safe state machine:
    - CLOSED: calls pass through; count failures.
    - OPEN: fail fast until reset timeout elapses.
    - HALF_OPEN: allow a probe; close on success or re-open on failure.
    """

    name: str
    failure_threshold: int
    reset_timeout_seconds: float
    success_threshold: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    opened_at: float | None = None
    transition_log: list[dict[str, str | float]] = field(default_factory=list)

    def allow_request(self) -> bool:
        """Return whether a request should be attempted.

        State-based admission control (the "fail fast" gate):
        - CLOSED    → always allow, provider is considered healthy.
        - HALF_OPEN → allow a single probe request to test recovery.
        - OPEN      → deny until reset_timeout_seconds has elapsed since
                      opened_at, then move to HALF_OPEN and allow one probe.
        """
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN:
            return True
        # OPEN — only admit once the cool-off window has elapsed.
        if self.opened_at is not None and (
            time.monotonic() - self.opened_at >= self.reset_timeout_seconds
        ):
            self._transition(CircuitState.HALF_OPEN, reason="reset_timeout_elapsed")
            return True
        return False

    def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Call a function through the circuit breaker.

        Fails fast when the circuit is open, otherwise wraps the call so that
        every outcome updates the state machine (no manual bookkeeping needed
        by callers).
        """
        if not self.allow_request():
            raise CircuitOpenError(f"circuit '{self.name}' is open")
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def record_success(self) -> None:
        """Record a successful call.

        A success clears the consecutive-failure counter. While probing
        (HALF_OPEN) we require ``success_threshold`` consecutive probes before
        declaring the provider healthy again (CLOSED).
        """
        self.failure_count = 0
        self.success_count += 1
        if (
            self.state == CircuitState.HALF_OPEN
            and self.success_count >= self.success_threshold
        ):
            self._transition(CircuitState.CLOSED, reason="probe_success")
            self.success_count = 0

    def record_failure(self) -> None:
        """Record a failed call.

        Two *distinct* triggers re-open the circuit, kept separate on purpose
        (if/elif, never combined with ``or``) so the transition log records
        *why* the circuit opened:
        - A failed HALF_OPEN probe re-opens immediately ("probe_failure").
        - Reaching the consecutive-failure threshold in CLOSED opens with
          "failure_threshold_reached".
        """
        self.failure_count += 1
        self.success_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN, reason="probe_failure")
            self.opened_at = time.monotonic()
        elif self.failure_count >= self.failure_threshold:
            self._transition(CircuitState.OPEN, reason="failure_threshold_reached")
            self.opened_at = time.monotonic()

    def _transition(self, new_state: CircuitState, reason: str) -> None:
        if self.state == new_state:
            return
        self.transition_log.append(
            {"from": self.state.value, "to": new_state.value, "reason": reason, "ts": time.time()}
        )
        self.state = new_state
