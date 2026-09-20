import functools
import inspect
import sys
import os
from typing import Any, Callable, List

from unify.events.event_bus import EVENT_BUS

from tests.settings import SETTINGS

# ---------- CURSOR DEBUG LOGGER --------------------------------
# Re-export the leaf logger from the production module to keep a single
# grep-able function name while avoiding circular imports in production code.
from unify.common.debug import CURSOR_DEBUG_LOG  # noqa: E402,F401


class _TestContext:
    """Per-call bookkeeping hook for ``_handle_project``; nothing to do now that
    the store is reset per test by the root conftest."""

    def setup(self) -> None:
        return None

    def teardown(self) -> None:
        return None


def _handle_project(
    test_fn: Callable | None = None,
    *,
    try_reuse_prev_ctx: bool = False,
    delete_ctx_on_exit: bool = False,
):
    """Mark a test that exercises the store. The per-test reset happens in the
    root conftest; this wrapper only preserves the call shape tests use."""
    if test_fn is None:
        return lambda f: _handle_project(
            f,
            try_reuse_prev_ctx=try_reuse_prev_ctx,
            delete_ctx_on_exit=delete_ctx_on_exit,
        )

    if inspect.iscoroutinefunction(test_fn):

        @functools.wraps(test_fn)
        async def wrapper(*args, **kwargs):
            ctx = _TestContext()
            ctx.setup()
            try:
                result = test_fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
            finally:
                ctx.teardown()

    else:

        @functools.wraps(test_fn)
        def wrapper(*args, **kwargs):
            ctx = _TestContext()
            ctx.setup()
            try:
                test_fn(*args, **kwargs)
            finally:
                ctx.teardown()

    return wrapper


# ---------------- Additional shared test helpers ----------------
import asyncio
import re
import uuid
import random

DEFAULT_TIMEOUT = 60


def _assert_non_empty_str(val: object) -> None:
    assert isinstance(val, str) and val.strip(), "Expected a non-empty string"


def _normalize_alnum_lower(s: str) -> str:
    return re.sub(r"\W+", "", s.strip().lower())


def _contains_any(text: str, substrings: list[str]) -> bool:
    t = text.lower()
    return any(sub.lower() in t for sub in substrings)


def _ack_ok(reply: str) -> bool:
    return _contains_any(
        reply,
        ["ack", "acknowledged", "noted", "received", "okay", "ok"],
    )


async def _assert_blocks_while_paused(result_or_coro, delay: float = 0.1):
    # Accept either a coroutine or a Task/Future; create a task only when needed.
    try:
        is_task = isinstance(result_or_coro, asyncio.Task)
        is_future = asyncio.isfuture(result_or_coro)
    except Exception:
        is_task = False
        is_future = False
    t = (
        result_or_coro
        if (is_task or is_future)
        else asyncio.create_task(result_or_coro)
    )
    await asyncio.sleep(delay)
    assert not t.done(), "result() should block while paused"
    return t


def _unique_token(prefix: str = "TOKEN") -> str:
    rand_bits = random.getrandbits(128)
    return f"{prefix}-{uuid.UUID(int=rand_bits, version=4)}"


def make_queues():
    up_q: asyncio.Queue[str] = asyncio.Queue()
    down_q: asyncio.Queue[str] = asyncio.Queue()
    return up_q, down_q


from contextlib import asynccontextmanager
from typing import List


@asynccontextmanager
async def capture_events(
    event_type: str,
    filter: str | None = None,
    every_n: int = 1,
):
    """
    Context manager to capture events live.
    Avoids slow/blocking backend searches in tests by hooking directly
    into the in-memory EventBus publishing pipeline.
    """
    captured: List[Any] = []

    async def _cb(evts):
        captured.extend(evts)

    sub_id = EVENT_BUS.register_callback(
        event_type=event_type,
        callback=_cb,
        filter=filter,
        every_n=every_n,
    )
    try:
        yield captured
    finally:
        # The callback for the final event may still be running; wait for it
        # so the test asserts on a complete capture.
        await EVENT_BUS.ajoin_callbacks()
        EVENT_BUS.unregister_callback(sub_id)


# ---------- Idempotent Seeding Helpers for Parallel Tests ----------
#
# These helpers enable safe parallel execution of tests that share scenario
# data. The approach is simple: make individual operations idempotent rather
# than using distributed locks.
#
# - get_or_create_contact: Handles race conditions at the contact level
# - rebuild_id_mapping: Reconstructs local state from existing shared data
# - is_scenario_seeded: Checks if scenario data already exists
# --------------------------------------------------------------------------


# ---------- File-Based Scenario Lock for Parallel Tests ----------
#
# For scenarios with high data volume or no unique constraints (like transcript
# messages), use this file lock to coordinate seeding across parallel processes.
# Only one process seeds while others wait, then all rebuild local state.
#
# For scenarios with low volume and DB-level uniqueness (like contacts), the
# simpler idempotent check-before-create pattern works fine without a lock.
# --------------------------------------------------------------------------

import tempfile
import time
from contextlib import contextmanager

# Cross-platform file locking
if sys.platform == "win32":
    import msvcrt

    def _lock_file_nb(file_obj):
        """Acquire an exclusive non-blocking lock on the file (Windows)."""
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(file_obj):
        """Release the lock on the file (Windows)."""
        file_obj.seek(0)
        msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file_nb(file_obj):
        """Acquire an exclusive non-blocking lock on the file (Unix)."""
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(file_obj):
        """Release the lock on the file (Unix)."""
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)


def _acquire_file_lock_with_timeout(
    lock_file,
    timeout: float,
    lock_name: str,
) -> None:
    """
    Acquire a file lock with timeout to prevent indefinite hangs.

    Uses non-blocking lock attempts with polling to implement a timeout.
    If the timeout is exceeded, raises TimeoutError with a descriptive message.

    This prevents a hung process from blocking all other parallel tests
    indefinitely - instead, waiting tests will fail with a clear error.
    """
    start = time.monotonic()
    last_log = start
    waiting_logged = False
    while True:
        try:
            _lock_file_nb(lock_file)
            if waiting_logged:
                print(
                    f"[lock] Acquired '{lock_name}' after "
                    f"{time.monotonic() - start:.1f}s",
                    flush=True,
                )
            return  # Successfully acquired lock
        except (BlockingIOError, OSError):
            now = time.monotonic()
            elapsed = now - start
            if elapsed >= timeout:
                raise TimeoutError(
                    f"Timeout after {timeout}s waiting for lock '{lock_name}'. "
                    f"Another test process may be hung while holding this lock.",
                )
            # Log immediately on first wait, then every 30s so CI logs show
            # lock starvation instead of a silent session-timeout kill.
            if (not waiting_logged) or (now - last_log >= 30.0):
                print(
                    f"[lock] Waiting for '{lock_name}' "
                    f"({elapsed:.0f}s / {timeout:.0f}s)...",
                    flush=True,
                )
                waiting_logged = True
                last_log = now
            time.sleep(0.1)  # Brief sleep before retry


@contextmanager
def scenario_file_lock(lock_name: str, timeout: float | None = None):
    """
    File-based lock for coordinating parallel test scenario seeding.

    Use this when seeding involves high data volume or resources without
    unique constraints (e.g., transcript messages). All parallel processes
    block on this lock, ensuring only one seeds at a time.

    Args:
        lock_name: Unique name for this scenario's lock file.
                   Will be created in system temp directory.
        timeout: Maximum seconds to wait for the lock. Defaults to
                 SETTINGS.UNIFY_FILE_LOCK_TIMEOUT (3600s / 1 hour).

    Raises:
        TimeoutError: If the lock cannot be acquired within the timeout.
            This indicates another process is hung while holding the lock.

    Example:
        with scenario_file_lock("tm_scenario"):
            if is_scenario_seeded(cm, CONTACTS, transcript_context=ctx):
                # Rebuild local state
                ids = rebuild_id_mapping(cm, CONTACTS)
            else:
                # Seed the scenario
                seed_all_data()
    """
    if timeout is None:
        timeout = SETTINGS.UNIFY_FILE_LOCK_TIMEOUT
    lock_path = os.path.join(tempfile.gettempdir(), f"unity_{lock_name}.lock")
    lock_file = open(lock_path, "w")
    try:
        _acquire_file_lock_with_timeout(lock_file, timeout, lock_name)
        yield
    finally:
        _unlock_file(lock_file)
        lock_file.close()


@contextmanager
def mutation_test_lock(lock_name: str, timeout: float | None = None):
    """
    File-based lock for serializing mutation tests in parallel execution.

    When tests run via parallel_run.sh (per-test concurrency), tests that
    mutate shared context data can race with each other's rollbacks. This
    lock ensures only one mutation test runs at a time, preventing:

    1. Test A updates data
    2. Test B starts and rolls back (wiping A's changes)
    3. Test A's verification fails

    Read-only tests don't need this lock and can run fully in parallel.

    Args:
        lock_name: Unique name for this lock (e.g., "cm_mutation").
                   Will be created in system temp directory.
        timeout: Maximum seconds to wait for the lock. Defaults to
                 SETTINGS.UNIFY_FILE_LOCK_TIMEOUT (3600s / 1 hour).

    Raises:
        TimeoutError: If the lock cannot be acquired within the timeout.
            This indicates another process is hung while holding the lock.

    Example:
        @pytest.fixture
        def mutation_scenario(base_scenario):
            cm, id_map = base_scenario
            with mutation_test_lock("cm_mutation"):
                yield cm, id_map
    """
    if timeout is None:
        timeout = SETTINGS.UNIFY_FILE_LOCK_TIMEOUT
    lock_path = os.path.join(tempfile.gettempdir(), f"unity_{lock_name}.lock")
    lock_file = open(lock_path, "w")
    try:
        _acquire_file_lock_with_timeout(lock_file, timeout, lock_name)
        yield
    finally:
        _unlock_file(lock_file)
        lock_file.close()
