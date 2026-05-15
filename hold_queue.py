"""Per-call hold queue for the BC voice agent.

When an escalation fires and (active Twilio conferences + queued
dispatches) >= MAX_CONCURRENT_REPS, the customer lands here instead of
running the 60-second probe straight away. Customers hold in FIFO order;
when a slot frees, the head-of-queue customer is dispatched into the
existing probe flow.

Single-process design (v1): one event loop, one `_QUEUE` list, one
`asyncio.Lock`. Multi-instance support requires swapping `_QUEUE` for a
Twilio Sync List (slice 8 / v2). Until then, "deploy" means "drain" —
the graceful-shutdown hook (slice 5) flushes in-flight entries as
`abandoned_in_queue` tickets.

Gated by `QUEUE_ENABLED` env var; this module is dark code until
escalation.py wires it in at slice 4.

Plan: ~/.claude/plans/crystalline-sleeping-aho.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional

from twilio.rest import Client

logger = logging.getLogger(__name__)


# === Tunables (read from env each call so live `cartesia env set` takes
#     effect without a redeploy) ============================================

def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "queue: invalid %s=%r in env; using default %d", name, raw, default
        )
        return default


def _max_concurrent_reps() -> int:
    return _int_env("MAX_CONCURRENT_REPS", 2)


def _poll_interval_seconds() -> int:
    return _int_env("QUEUE_POLL_INTERVAL_SECONDS", 15)


# === Data class ============================================================

@dataclass
class QueueEntry:
    """One waiting customer. `completed_flag` is a shared closure-cell
    list[bool] — the same instance used by escalate_to_human and main.py's
    CallEnded wrapper — so the dispatch loop can detect a hangup-while-
    queued without needing extra coordination."""
    call_id: str
    caller_number: str
    intent_summary: str
    entered_at: float
    completed_flag: list  # single-element list[bool]


# === Module-level state ====================================================

_QUEUE: list[QueueEntry] = []
_QUEUE_LOCK = asyncio.Lock()
_ACTIVE_PROBES: int = 0  # customers dequeued + currently in their probe wait
_SLOT_AVAILABLE_EVENT = asyncio.Event()
_POLLER_TASK: Optional[asyncio.Task] = None
_TWILIO_CLIENT: Optional[Client] = None


def _twilio_client() -> Client:
    """Lazy-build the Twilio REST client on first use. Avoids per-poll
    allocation but doesn't fail at import time if env vars aren't set
    (the module loads dark; the client is only needed once the poller
    actually starts)."""
    global _TWILIO_CLIENT
    if _TWILIO_CLIENT is None:
        sid = os.environ["TWILIO_ACCOUNT_SID"]
        token = os.environ["TWILIO_AUTH_TOKEN"]
        _TWILIO_CLIENT = Client(sid, token)
    return _TWILIO_CLIENT


# === Public API ============================================================

async def enqueue(entry: QueueEntry) -> int:
    """Append a customer to the queue. Returns the 1-indexed position
    (1 = head, 2 = behind one customer, …). Idempotently starts the
    shared poller if it isn't already running."""
    async with _QUEUE_LOCK:
        _QUEUE.append(entry)
        pos = len(_QUEUE)
    await start_shared_poller()
    logger.info(
        "queue.enqueue call_id=%s caller=%s position=%d depth=%d",
        entry.call_id, entry.caller_number, pos, len(_QUEUE),
    )
    return pos


async def try_admit() -> bool:
    """Atomically attempt to claim a slot bypassing the queue. Returns
    True iff the queue is empty AND a Twilio slot is free; in that case
    _ACTIVE_PROBES is incremented and the caller proceeds straight to
    the probe path. Returns False otherwise — caller should enqueue +
    wait_for_dispatch.

    FIFO discipline is preserved: if anyone is already queued, a new
    arrival queues behind them even if a slot would otherwise be free.
    Same race-prevention as wait_for_dispatch (lock + counter).
    """
    global _ACTIVE_PROBES
    async with _QUEUE_LOCK:
        if _QUEUE:
            return False
        active_confs = _count_active_conferences()
        if active_confs + _ACTIVE_PROBES >= _max_concurrent_reps():
            return False
        _ACTIVE_PROBES += 1
        logger.info(
            "queue.try_admit DIRECT-ADMIT active_confs=%d active_probes=%d max=%d",
            active_confs, _ACTIVE_PROBES, _max_concurrent_reps(),
        )
        return True


async def position(call_id: str) -> Optional[int]:
    """1-indexed position of `call_id` in the queue, or None if not
    queued. O(n) scan; fine for the v1 cap of ~10 waiters."""
    async with _QUEUE_LOCK:
        for i, entry in enumerate(_QUEUE):
            if entry.call_id == call_id:
                return i + 1
    return None


async def dequeue(call_id: str) -> None:
    """Remove a queued caller. Idempotent — safe to call when the caller
    isn't queued (already dispatched, or never enqueued). Called by
    main.py's CallEnded wrapper on hangup-while-queued (Case 10)."""
    async with _QUEUE_LOCK:
        for i, entry in enumerate(_QUEUE):
            if entry.call_id == call_id:
                _QUEUE.pop(i)
                logger.info(
                    "queue.dequeue call_id=%s depth=%d", call_id, len(_QUEUE)
                )
                return
    # Not present — silently OK (idempotency).


async def wait_for_dispatch(call_id: str, completed_flag: list) -> bool:
    """Block until this caller can be dispatched to the probe path OR
    the caller hangs up. Returns:
      - True: caller is head-of-queue AND a slot is free. _ACTIVE_PROBES
        has been incremented and the caller has been popped from the
        queue. Caller's coroutine should now run the probe flow and
        `await release_probe_slot()` when done.
      - False: hangup (completed_flag[0] went True). Caller is NOT
        popped here — main.py's CallEnded handler does that via dequeue.

    Defense-in-depth on hangup detection: this loop polls completed_flag
    each iteration AND the CallEnded handler explicitly dequeues.
    Either alone is sufficient; both together survive Cartesia event-
    delivery flakiness.
    """
    while True:
        if completed_flag and completed_flag[0]:
            logger.info("queue.wait_for_dispatch HANGUP call_id=%s", call_id)
            return False

        # Wait for the next slot-available signal, with a timeout so we
        # re-check completed_flag periodically even if the poller doesn't
        # fire (Twilio API blip, etc.).
        try:
            await asyncio.wait_for(
                _SLOT_AVAILABLE_EVENT.wait(),
                timeout=_poll_interval_seconds(),
            )
        except asyncio.TimeoutError:
            continue

        async with _QUEUE_LOCK:
            global _ACTIVE_PROBES

            # Clear the event under lock so the next wake must come from
            # the poller. Prevents tight-loop spin after one head-of-
            # queue waiter takes the slot but the event was still set.
            _SLOT_AVAILABLE_EVENT.clear()

            # Am I still head-of-queue? (Might have been dequeued by a
            # hangup-race between event fire and lock acquire.)
            if not _QUEUE or _QUEUE[0].call_id != call_id:
                continue

            # Slot occupancy check, done under lock so two coroutines
            # can't both pass.
            active_confs = _count_active_conferences()
            if active_confs + _ACTIVE_PROBES >= _max_concurrent_reps():
                continue

            # Commit: claim the slot, dequeue self, return True.
            _ACTIVE_PROBES += 1
            _QUEUE.pop(0)
            logger.info(
                "queue.wait_for_dispatch DISPATCHED call_id=%s "
                "active_probes=%d depth=%d",
                call_id, _ACTIVE_PROBES, len(_QUEUE),
            )
            return True


async def release_probe_slot() -> None:
    """Decrement _ACTIVE_PROBES when a dispatched caller's probe finishes
    (transferred OR fell through to callback intake). Called by
    escalation.py at slice 4 wire-up time. Wakes any queue waiters."""
    global _ACTIVE_PROBES
    async with _QUEUE_LOCK:
        _ACTIVE_PROBES = max(0, _ACTIVE_PROBES - 1)
    _SLOT_AVAILABLE_EVENT.set()


async def start_shared_poller() -> None:
    """Idempotent: starts the polling task if it isn't already running.
    The task runs for the lifetime of the process (one Twilio API call
    per poll interval; cost is trivial)."""
    global _POLLER_TASK
    if _POLLER_TASK is not None and not _POLLER_TASK.done():
        return
    _POLLER_TASK = asyncio.create_task(_poller_loop())
    logger.info("queue: shared poller started")


# === Internals =============================================================

def _count_active_conferences() -> int:
    """Twilio's Python SDK is sync — this call blocks the event loop for
    the HTTP roundtrip (~100–500ms). Acceptable at 15s poll cadence;
    revisit if poll interval ever needs to drop below ~5s (consider
    httpx + the REST API directly, async)."""
    try:
        confs = _twilio_client().conferences.list(
            status="in-progress", limit=20
        )
        return len(confs)
    except Exception:
        logger.exception(
            "queue: Twilio conferences.list failed; treating as 0"
        )
        return 0


async def _poller_loop() -> None:
    """Forever loop. Each iteration: sleep, then under the lock count
    Twilio active conferences. If (active_confs + _ACTIVE_PROBES) is
    below MAX_CONCURRENT_REPS, set the slot-available event to wake all
    waiters. The head-of-queue waiter wins under the lock."""
    while True:
        try:
            await asyncio.sleep(_poll_interval_seconds())
            async with _QUEUE_LOCK:
                if not _QUEUE:
                    continue
                active_confs = _count_active_conferences()
                max_reps = _max_concurrent_reps()
                if active_confs + _ACTIVE_PROBES < max_reps:
                    _SLOT_AVAILABLE_EVENT.set()
                    logger.info(
                        "queue: slot available — depth=%d active_confs=%d "
                        "active_probes=%d max=%d",
                        len(_QUEUE), active_confs, _ACTIVE_PROBES, max_reps,
                    )
        except asyncio.CancelledError:
            logger.info("queue: poller cancelled")
            raise
        except Exception:
            logger.exception(
                "queue: poller loop iteration crashed; continuing"
            )
