"""Provider-neutral run contract and a deterministic local fake adapter.

The local adapter is for development/tests only. Per-run directories are separate
workspaces, not OS/container security boundaries; production cloud workers must add
process/container isolation, durable storage, and provider-specific authentication.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


_MAX_PAYLOAD_BYTES = 1_000_000
_MAX_RESULT_BYTES = 250_000
_MAX_TIMEOUT_SECONDS = 86_400
_MAX_ERROR_LENGTH = 1_000
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


_TERMINAL = {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.TIMED_OUT, RunStatus.FAILED}


class RunError(RuntimeError):
    """Base exception for invalid run requests and runner operations."""


class IdempotencyConflict(RunError):
    """An idempotency key was reused for a different request."""


class RunCancelled(RunError):
    """Raised when a cooperative worker observes cancellation."""


class RunTimedOut(RunError):
    """Raised when a cooperative worker observes its deadline."""


@dataclass(frozen=True, init=False)
class RunRequest:
    """Immutable JSON request accepted by any conforming runner adapter."""

    idempotency_key: str
    timeout_seconds: float
    _payload_json: str = field(repr=False)

    def __init__(self, idempotency_key: str, payload: Mapping[str, Any], timeout_seconds: float = 900):
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("idempotency_key must be 1-200 safe characters")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be a JSON object")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a finite number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 0 and {_MAX_TIMEOUT_SECONDS}")
        try:
            payload_json = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain only JSON-safe values") from exc
        if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the safe 1 MB limit")
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "_payload_json", payload_json)

    @property
    def payload(self) -> dict[str, Any]:
        """Return a fresh payload copy so callers cannot mutate the request."""
        return json.loads(self._payload_json)

    @property
    def fingerprint(self) -> str:
        material = f"{self.timeout_seconds}:{self._payload_json}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class RunEvent:
    status: RunStatus
    timestamp: float
    detail: str


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    status: RunStatus
    created_at: float
    updated_at: float
    result: Any = None
    error: str | None = None
    events: tuple[RunEvent, ...] = ()


@runtime_checkable
class Runner(Protocol):
    """Minimum provider-neutral interface for submitting and controlling runs."""

    def submit(self, request: RunRequest) -> RunSnapshot: ...
    def get(self, run_id: str) -> RunSnapshot | None: ...
    def cancel(self, run_id: str) -> RunSnapshot: ...


class RunExecutionContext:
    """Cooperative cancellation/deadline checks exposed to a worker callback."""

    def __init__(self, workspace: Path, cancel_event: threading.Event, deadline: float):
        self.workspace = workspace
        self._cancel_event = cancel_event
        self._deadline = deadline

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_event.is_set()

    def check_cancelled(self) -> None:
        if time.monotonic() >= self._deadline:
            raise RunTimedOut("Run deadline reached")
        if self._cancel_event.is_set():
            raise RunCancelled("Run cancellation requested")


@dataclass
class _RunState:
    run_id: str
    request: RunRequest
    fingerprint: str
    status: RunStatus
    created_at: float
    updated_at: float
    workspace: Path
    cancel_event: threading.Event
    deadline: float
    events: list[RunEvent]
    result_json: str | None = None
    error: str | None = None
    future: Future | None = None


class LocalFakeRunner:
    """Async, in-memory fake runner with per-run workspaces for local testing.

    Results and failures are stored in memory only. Cancellation/deadlines are
    cooperative: handlers should call ``context.check_cancelled()`` at safe points.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        handler: Callable[[RunRequest, RunExecutionContext], Any],
        *,
        max_workers: int = 4,
    ):
        if not callable(handler):
            raise TypeError("handler must be callable")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        self._root = Path(workspace_root).expanduser()
        if self._root.is_symlink():
            raise OSError("Workspace root must not be a symlink")
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._root.is_symlink() or not self._root.is_dir():
            raise OSError("Workspace root must be a real directory")
        self._root = self._root.resolve()
        if os.name == "posix":
            self._root.chmod(0o700)
        self._handler = handler
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="niji-fake-run")
        self._lock = threading.RLock()
        self._runs: dict[str, _RunState] = {}
        self._by_idempotency_key: dict[str, str] = {}
        self._closed = False

    def submit(self, request: RunRequest) -> RunSnapshot:
        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        with self._lock:
            if self._closed:
                raise RunError("Runner is closed")
            prior_id = self._by_idempotency_key.get(request.idempotency_key)
            if prior_id is not None:
                prior = self._runs[prior_id]
                if prior.fingerprint != request.fingerprint:
                    raise IdempotencyConflict("Idempotency key already belongs to a different request")
                return self._snapshot(prior)

            run_id = uuid.uuid4().hex
            workspace = self._create_workspace(run_id)
            now = time.time()
            state = _RunState(
                run_id=run_id,
                request=request,
                fingerprint=request.fingerprint,
                status=RunStatus.QUEUED,
                created_at=now,
                updated_at=now,
                workspace=workspace,
                cancel_event=threading.Event(),
                deadline=time.monotonic() + request.timeout_seconds,
                events=[RunEvent(RunStatus.QUEUED, now, "Run accepted")],
            )
            self._runs[run_id] = state
            self._by_idempotency_key[request.idempotency_key] = run_id
            try:
                state.future = self._executor.submit(self._execute, run_id)
            except Exception:
                self._runs.pop(run_id, None)
                self._by_idempotency_key.pop(request.idempotency_key, None)
                self._remove_workspace(workspace)
                raise
            return self._snapshot(state)

    def get(self, run_id: str) -> RunSnapshot | None:
        with self._lock:
            state = self._runs.get(run_id)
            return self._snapshot(state) if state else None

    def cancel(self, run_id: str) -> RunSnapshot:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                raise KeyError("Unknown run id")
            if state.status in _TERMINAL:
                return self._snapshot(state)
            state.cancel_event.set()
            if state.status == RunStatus.QUEUED and state.future is not None and state.future.cancel():
                self._transition(state, RunStatus.CANCELLED, "Cancelled before execution")
            elif state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                self._transition(state, RunStatus.CANCELLING, "Cancellation requested")
            return self._snapshot(state)

    def workspace_for(self, run_id: str) -> Path:
        """Return the local fake adapter's workspace; never part of cloud contract."""
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                raise KeyError("Unknown run id")
            return state.workspace

    def purge(self, run_id: str) -> bool:
        """Delete a terminal run's workspace explicitly; active runs cannot be purged."""
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return False
            if state.status not in _TERMINAL:
                raise RunError("Cannot purge an active run")
            return self._remove_workspace(state.workspace)

    def close(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for state in self._runs.values():
                if state.status in _TERMINAL:
                    continue
                state.cancel_event.set()
                if state.status == RunStatus.QUEUED and state.future is not None and state.future.cancel():
                    self._transition(state, RunStatus.CANCELLED, "Runner closed before execution")
                elif state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                    self._transition(state, RunStatus.CANCELLING, "Runner shutdown requested cancellation")
        # Workers acquire the same lock when publishing terminal state; never hold it
        # while waiting for executor shutdown.
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def _create_workspace(self, run_id: str) -> Path:
        path = self._root / run_id
        path.mkdir(mode=0o700)
        if path.is_symlink() or path.resolve().parent != self._root:
            raise OSError("Run workspace escaped its isolation root")
        if os.name == "posix":
            path.chmod(0o700)
        return path

    def _remove_workspace(self, path: Path) -> bool:
        # Only remove a real directory directly under the configured root. Do not
        # follow a replaced symlink or recursively delete an unexpected location.
        try:
            if (path.is_symlink() or not path.is_dir()
                    or path.parent.resolve() != self._root):
                return False
            shutil.rmtree(path)
            return not path.exists()
        except OSError:
            return False

    def _execute(self, run_id: str) -> None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None or state.status in _TERMINAL:
                return
            if state.cancel_event.is_set():
                self._transition(state, RunStatus.CANCELLED, "Cancelled before execution")
                return
            self._transition(state, RunStatus.RUNNING, "Worker started")
            context = RunExecutionContext(state.workspace, state.cancel_event, state.deadline)
            request = state.request

        try:
            context.check_cancelled()
            result = self._handler(request, context)
            context.check_cancelled()
            result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(result_json.encode("utf-8")) > _MAX_RESULT_BYTES:
                raise ValueError("result exceeds the safe 250 KB limit")
        except RunCancelled:
            target, detail, error = RunStatus.CANCELLED, "Run cancelled", None
        except RunTimedOut:
            target, detail, error = RunStatus.TIMED_OUT, "Run timed out", "Run deadline reached"
        except Exception as exc:
            # Do not expose arbitrary exception text: it can contain credentials,
            # prompt contents, or other private data from a provider/handler.
            target = RunStatus.FAILED
            detail = "Run failed"
            error = f"Execution failed ({type(exc).__name__})"[:_MAX_ERROR_LENGTH]
        else:
            with self._lock:
                state = self._runs.get(run_id)
                if state is None:
                    return
                state.result_json = result_json
            target, detail, error = RunStatus.COMPLETED, "Run completed", None

        with self._lock:
            state = self._runs.get(run_id)
            if state is None or state.status in _TERMINAL:
                return
            # Cancellation already requested should win unless the deadline expired.
            if state.status == RunStatus.CANCELLING and target == RunStatus.COMPLETED:
                target, detail = RunStatus.CANCELLED, "Run cancelled"
            self._transition(state, target, detail, error=error)

    def _transition(
        self,
        state: _RunState,
        target: RunStatus,
        detail: str,
        *,
        error: str | None = None,
    ) -> None:
        allowed = {
            RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.CANCELLED},
            RunStatus.RUNNING: {RunStatus.CANCELLING, RunStatus.COMPLETED, RunStatus.CANCELLED,
                                RunStatus.TIMED_OUT, RunStatus.FAILED},
            RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.TIMED_OUT, RunStatus.FAILED},
        }
        if target not in allowed.get(state.status, set()):
            raise RunError(f"Invalid lifecycle transition: {state.status.value} -> {target.value}")
        now = time.time()
        state.status = target
        state.updated_at = now
        state.error = error
        if target != RunStatus.COMPLETED:
            state.result_json = None
        state.events.append(RunEvent(target, now, detail[:200]))

    @staticmethod
    def _snapshot(state: _RunState) -> RunSnapshot:
        result = json.loads(state.result_json) if state.result_json is not None else None
        return RunSnapshot(
            run_id=state.run_id,
            status=state.status,
            created_at=state.created_at,
            updated_at=state.updated_at,
            result=result,
            error=state.error,
            events=tuple(state.events),
        )
