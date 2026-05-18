"""Escalate the call to a human via a single passthrough tool.

The tool handles the entire escalation flow itself:
  1. Speaks an announcement to the caller (no LLM-text-before-tool race).
  2. Fires a Slack call-ping with a "Take call in browser" button.
  3. Runs the probe (polls the Twilio conference for a participant;
     5s sleep in demo mode).
  4. Resolves:
       - Available (rep joined): waits for the announcement to finish
         playing, then force-redirects the customer's call to the
         conference via Twilio REST API call.update. Bridges the caller
         to the rep.
       - Unavailable (probe timed out OR force-redirect failed): speaks
         "Sorry, all our lines are busy — what's your full name?" Then
         the LLM handles callback intake (name + phone, calling
         record_followup). Email path still works if the caller
         proactively asks, but the bot doesn't offer it.

Why force-redirect instead of Cartesia's AgentTransferCall:
  AgentTransferCall issues `<Dial>` from the customer's call leg.
  Twilio's voice routing has been observed to return "busy" when the
  originating call came in on a toll-free, killing the customer's call
  with no fallback. The REST API call.update mechanism doesn't place a
  new outbound call — it just rewrites the existing call's TwiML — so
  it sidesteps the toll-free routing issue entirely. Failures here are
  also detectable, so we can fall through to callback intake.

Why passthrough instead of loopback:
  LLMs (Haiku and friends) unreliably interleave text generation with
  tool calls, and don't always chain multiple tool calls cleanly.
  Putting all the speech INSIDE the tool — yielding AgentSendText events
  directly — sidesteps that whole class of bug.
"""

import asyncio
import logging
import os
import re
import time
from typing import Annotated, Optional
from urllib.parse import quote

import httpx
from line.events import AgentSendText
from line.llm_agent.tools.decorators import passthrough_tool
from line.voice_agent_app import CallRequest
from twilio.rest import Client

import hold_queue
from hold_queue import QueueEntry
from linear_ticket import log_call_complete, log_escalation_started
from slack_ticket import send_queue_entry_ping

logger = logging.getLogger(__name__)


_PROBE_TIMEOUT_DEFAULT = 60  # upper bound default; overridable via PROBE_TIMEOUT_SECONDS env var


def _probe_timeout_seconds() -> int:
    """How long the probe waits for someone to join the conference
    before falling through to callback intake. Read fresh per call so
    `cartesia env set` takes effect without a code redeploy. Defaults
    to 60s (tight enough that production reps watching Slack actively
    answer in time; staging can override to 180s+ for solo testing)."""
    return _int_env("PROBE_TIMEOUT_SECONDS", _PROBE_TIMEOUT_DEFAULT)


POLL_INTERVAL_SECONDS = 1

# Module-level reference to keep probe tasks alive across LLM hijacks.
# Background: escalate_to_human is an async generator. When the LLM
# hijacks the tool mid-wait (caller spoke during a filler gap), Cartesia
# may garbage-collect the generator. Without an external reference, the
# probe task spawned inside it could be cancelled too — and the
# force-redirect to the conference would never fire. Storing the task
# here pins it until it completes naturally (participant join or
# timeout). The done-callback cleans up so the set doesn't grow.
_ACTIVE_PROBE_TASKS: set = set()

# Filler audio played to the customer during the probe wait. Spoken in
# order; varied wording so it doesn't sound like a loop. The list covers
# the full PROBE_TIMEOUT_SECONDS window — first filler addresses "are you
# still there?" head-on so callers don't have to ask.
#
# Interval is short on purpose: longer silent gaps between fillers let
# unrelated room noise / side conversations leak into Cartesia's STT,
# which can trigger the LLM mid-tool and break out of the escalate flow.
# Keeping each gap to ~1s vs filler ~3s gives Cartesia almost no quiet
# window to misinterpret as user input.
PROBE_FILLER_INTERVAL_SECONDS = 10
# Wording deliberately avoids "one moment" / "one second" — those phrases
# are already in the escalation announcements (e.g., "Sure — one moment,
# connecting you to our team."), so echoing them in the first filler
# 10 seconds later sounded repetitive.
PROBE_FILLERS = [
    "Yep, still here — reaching out to our team now.",
    "Bear with me — almost there.",
    "Hang tight, getting someone on the line.",
    "Really appreciate your patience — won't be long.",
    "Still trying — should just be a few more seconds.",
    "Thanks for holding — really close now.",
    "Just a moment more — pinging the team.",
    "Almost there — appreciate you staying on.",
    "Hang in there — should just be a few more seconds.",
    "Bear with me — getting someone over now.",
    "Still here with you — won't be much longer.",
    "Almost connected — thanks for holding.",
    "Just another second — really appreciate your patience.",
    "Hold tight — connecting any moment now.",
]

# Spoken on the probe-failed path (nobody clicked the Slack button
# within PROBE_TIMEOUT_SECONDS) AND when the force-redirect REST API
# call fails (call already ended, Twilio outage). The bot leads the
# caller straight into callback intake (name + number → record_followup).
# No voicemail/email branches — simpler flow, single happy path.
TEAM_UNAVAILABLE_MESSAGE = (
    "Sorry, all our lines are busy right now. Let me grab your "
    "name and a callback number so someone can follow up on the "
    "next business day — what's your full name?"
)

# Spoken when an after-hours caller explicitly asks for a human. No
# probe runs — the team isn't online to pick up — so we go straight to
# callback intake. Phrasing avoids "lines are busy" (misleading;
# they're closed, not busy).
AFTER_HOURS_UNAVAILABLE_MESSAGE = (
    "Let me grab your name and a callback number so someone can "
    "follow up on the next business day — what's your full name?"
)

# Spoken when a queued caller's MAX_QUEUE_WAIT_SECONDS elapses without a
# rep freeing up. Acknowledges the wait + transitions to the standard
# callback-intake flow (the LLM then drives name + number + record_followup).
QUEUE_TIMEOUT_INTAKE_MESSAGE = (
    "Thanks for holding — sorry the wait's running long. Let me grab your "
    "name and a callback number and we'll reach out as soon as a rep "
    "frees up. What's your full name?"
)

# Spoken when a queued caller's turn finally comes up. Replaces the
# LLM-passed `spoken_announcement` for the queue path (the original was
# minted for a direct-admit context where the caller hasn't heard
# anything yet; here they've already heard the queue-entry message and
# position updates, so the wording needs to acknowledge that wait).
QUEUE_DISPATCH_TRANSITION_MESSAGE = (
    "Got a rep for you now — one moment."
)

# Spoken in the v2 handoff path RIGHT BEFORE the REST call.update moves
# the caller's leg to Twilio. Hardcoded (not LLM-supplied) so the user
# gets unambiguous wording every time, regardless of what Haiku chose
# for spoken_announcement. Kept short — Twilio's /enqueue-customer Say
# (spoken right after the handoff) introduces "our team" + the press-1
# option, so this message just acknowledges the transition. Music
# starts naturally so we don't promise it here (was a redundancy).
V2_TRANSFER_ANNOUNCEMENT = (
    "One moment — transferring you now."
)


def _env(name: str, *, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _int_env(name: str, default: int) -> int:
    """Same as hold_queue._int_env but local to avoid the cross-module
    coupling for what's a small helper."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r in env; using default %d", name, raw, default)
        return default


def _queue_enabled() -> bool:
    return os.environ.get("QUEUE_ENABLED", "").strip().lower() in ("1", "true", "yes")


async def _yield_callback_intake_message(
    *, after_hours: bool = False, queue_timeout: bool = False
):
    """Speak the message that leads the caller into name+number intake.
    Picks phrasing for the situation; the LLM drives the rest of the
    intake (asks for name → number → calls record_followup). Called from
    the probe-failure tail of run_escalation_flow AND from the queue
    hard-timeout branch of _wait_in_queue.
    """
    if queue_timeout:
        msg = QUEUE_TIMEOUT_INTAKE_MESSAGE
    elif after_hours:
        msg = AFTER_HOURS_UNAVAILABLE_MESSAGE
    else:
        msg = TEAM_UNAVAILABLE_MESSAGE
    yield AgentSendText(text=msg, interruptible=False)


async def _wait_in_queue(
    *,
    call_id: str,
    caller_number: str,
    intent_summary: str,
    completed_flag,
    escalation_status,
):
    """Queue admission tail: enqueue this caller, yield position updates
    until dispatched / hard-timeout / hangup. Yields agent events.

    On exit, `escalation_status["phase"]` indicates the outcome:
      - "probe_wait": caller was dispatched; _ACTIVE_PROBES was
        incremented inside hold_queue.wait_for_dispatch. Caller should
        proceed into the probe path. Caller's finally must call
        hold_queue.release_probe_slot.
      - "idle": hangup (caller left mid-queue) OR hard-timeout (already
        yielded the intake message). Caller should return without
        running the probe.
    """
    entry = QueueEntry(
        call_id=call_id,
        caller_number=caller_number,
        intent_summary=intent_summary,
        entered_at=time.time(),
        completed_flag=completed_flag,
    )
    escalation_status["phase"] = "queue_wait"
    position = await hold_queue.enqueue(entry)

    yield AgentSendText(
        text=(
            f"All our reps are with customers right now. You're number "
            f"{position} in line — hang tight, we'll connect you as soon "
            f"as someone frees up."
        ),
        interruptible=False,
    )

    # Linear paper trail for the queue entry. Pinned background task so
    # generator cancellation doesn't drop it (same pattern as
    # log_escalation_started).
    queue_log_task = asyncio.create_task(
        log_call_complete(
            call_id=call_id,
            caller_number=caller_number,
            caller_name=None,
            intent_summary=intent_summary,
            outcome="queue_waiting",
            recap=f"Caller queued at position {position}; awaiting dispatch.",
        )
    )
    _ACTIVE_PROBE_TASKS.add(queue_log_task)
    queue_log_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)

    # Slack ping (informational; no button until dispatch).
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        try:
            await send_queue_entry_ping(
                slack_url,
                caller_number=caller_number,
                intent_summary=intent_summary,
                position=position,
            )
        except Exception as e:
            logger.warning("queue-entry Slack ping failed (non-fatal): %s", e)

    update_interval = _int_env("QUEUE_POSITION_UPDATE_INTERVAL_SECONDS", 45)
    checkin_interval = _int_env("QUEUE_CHECKIN_INTERVAL_SECONDS", 180)
    max_wait = _int_env("MAX_QUEUE_WAIT_SECONDS", 900)
    started = time.monotonic()
    last_position_update_at = started
    last_checkin_at = started

    while True:
        now = time.monotonic()
        elapsed = now - started
        if elapsed >= max_wait:
            # Case 11: hard-timeout safety floor → callback intake.
            # Acts as a backstop for forgotten-phone-on-desk scenarios.
            # In normal use the periodic check-ins should give the
            # caller an explicit opt-out well before this fires.
            logger.info(
                "queue hard-timeout call_id=%s elapsed=%.0fs", call_id, elapsed
            )
            async for ev in _yield_callback_intake_message(queue_timeout=True):
                yield ev
            await hold_queue.dequeue(call_id)
            escalation_status["phase"] = "idle"
            return

        # Compute when each timer next needs to fire and wait for the
        # earliest event (dispatch / position update / check-in / max).
        time_to_position_update = (last_position_update_at + update_interval) - now
        time_to_checkin = (last_checkin_at + checkin_interval) - now
        time_to_max = max_wait - elapsed
        wait_for_timeout = max(0.1, min(
            time_to_position_update, time_to_checkin, time_to_max
        ))

        try:
            dispatched = await asyncio.wait_for(
                hold_queue.wait_for_dispatch(call_id, completed_flag),
                timeout=wait_for_timeout,
            )
        except asyncio.TimeoutError:
            now = time.monotonic()
            new_pos = await hold_queue.position(call_id)
            if new_pos is None:
                continue  # already dequeued (record_followup opt-out, etc.)
            # Check-in supersedes a regular position update at the
            # 3-min / 6-min / etc. boundaries. Gives the caller an
            # explicit opt-out without making the bot nag every 45s.
            if now >= last_checkin_at + checkin_interval:
                yield AgentSendText(
                    text=(
                        f"Still here — you're number {new_pos} in line. "
                        f"Want to keep waiting, or should I take a "
                        f"message and have someone call you back?"
                    ),
                    interruptible=False,
                )
                last_checkin_at = now
                # Reset position-update timer too — we just talked
                # about position, no need for another update 1s later.
                last_position_update_at = now
            elif now >= last_position_update_at + update_interval:
                yield AgentSendText(
                    text=f"Still here with you — you're number {new_pos} in line.",
                    interruptible=False,
                )
                last_position_update_at = now
            # else: timer was clamped (e.g., max_wait approaching) —
            # just loop and let the elapsed check fire next iteration.
            continue

        if not dispatched:
            # Case 10: hangup. main.py's CallEnded handler also calls
            # hold_queue.dequeue (defense-in-depth); we don't here.
            logger.info("queue dispatch returned hangup call_id=%s", call_id)
            escalation_status["phase"] = "idle"
            return

        # Dispatched — caller proceeds into probe path.
        escalation_status["phase"] = "probe_wait"
        logger.info(
            "queue dispatch SUCCESS call_id=%s — transitioning to probe", call_id
        )
        return


# === Pattern-based escalation detector =====================================
#
# Case 9 guarantee: if the customer says any of these phrases, the
# escalation flow runs regardless of whether the LLM decided to call
# escalate_to_human. The main.py wrapper detects this on each
# UserTurnEnded event and bypasses the LLM if matched + no escalation
# is already in progress. Better to over-match (occasional unwanted
# escalation that no-ops on the second trigger) than to miss a genuine
# human request.
_ESCALATION_PATTERNS = [
    # "speak to / talk to / chat with a human / representative / etc."
    # Verb-combined forms cover the legitimate "real person" requests
    # like "I want to speak to a real person" — there's no need for a
    # standalone "real/actual/live person" pattern that would also
    # match "Are you a real person?" (an AI-identity question, NOT an
    # escalation request — see Bot Identity section in main.py prompt).
    re.compile(
        r"\b(speak|talk|chat|connect|put|get|transfer|hand)\s+"
        r"(me\s+)?(to|with|over|through)\s+"
        r"(an?\s+)?"
        r"(human|person|someone|representative|rep|agent|manager|supervisor|"
        r"real\s+person|real\s+human|live\s+person|live\s+agent|"
        r"actual\s+human|actual\s+person)\b",
        re.I,
    ),
    # "want / need a human / representative / etc."
    re.compile(
        r"\b(want|need|prefer|require)\s+"
        r"(a\s+|an\s+|to\s+(speak|talk)\s+to\s+a?\s*)?"
        r"(human|person|representative|rep|agent|manager|supervisor)\b",
        re.I,
    ),
    # Standalone keywords that on their own are clear enough signals.
    re.compile(r"\brepresentative\b", re.I),
    re.compile(r"\bhuman\s+being\b", re.I),
]


def user_wants_human(text: str) -> bool:
    """True if the caller's transcribed turn unambiguously asks for a
    human. Called from main.py's per-turn wrapper as the Case 9 backstop
    — when this returns True and no escalation is already in progress,
    the wrapper bypasses the LLM and runs the escalation flow itself.
    """
    if not text:
        return False
    return any(p.search(text) for p in _ESCALATION_PATTERNS)


def _derive_conf_name(caller_number: str) -> str:
    """Per-call Twilio conference name derived from the caller's phone
    number.

    Why: a hardcoded conference name (e.g. 'bc-active') causes two
    simultaneous callers to land in the SAME room and overhear each
    other — privacy bug. Deriving from the caller's number gives each
    call its own room. Both the Python probe code and the Twilio
    Function compute the same name from the same caller number, so they
    coordinate without any shared state.

    Edge case: the same caller dialing twice within the probe window
    will still collide. Rare enough to accept for v1; the real fix
    needs cross-process state (Twilio Sync) and isn't worth the
    complexity yet.
    """
    digits = "".join(c for c in caller_number if c.isdigit())
    return f"bc-{digits}" if digits else "bc-active"


async def run_escalation_flow(
    *,
    call_id: str,
    caller_number: str,
    spoken_announcement: str,
    intent_summary: str,
    completed_flag=None,
    escalation_status=None,
    after_hours: bool = False,
):
    """The escalation flow body. Yields agent events. Called from two
    places:
      1. The escalate_to_human tool (LLM-initiated escalation).
      2. The main.py wrapper's Case 9 backstop (pattern-detected
         escalation that fires regardless of LLM decision).

    Single source of truth for the escalation behavior. The flow:
    speak the announcement → fire Slack ping → log "pending" Linear
    ticket → wait for a human (browser pickup or phone probe) → either
    transfer the caller to the conference OR speak the "lines are busy"
    callback intake prompt.

    `escalation_status` is an optional dict with key "phase" (string).
    Set to "probe_wait" at entry, reset to "idle" at exit so
    end_call_with_goodbye can detect Case 8 (LLM hijack ending the call
    mid-escalation). When QUEUE_ENABLED=true and all reps are busy, the
    flow diverts into the queue path first (phase="queue_wait") and
    transitions to "probe_wait" on dispatch — see _wait_in_queue.

    `after_hours=True` short-circuits the probe: speak the announcement,
    fire a no-button Slack FYI ping + the pending Linear ticket, then go
    straight to the after-hours callback intake prompt. No probe, no
    fillers, no transfer attempt — there's no one to click the Slack
    button. The LLM continues with name + number → record_followup.
    """
    browser_pickup_for_admit = (
        os.environ.get("BROWSER_PICKUP", "").strip().lower()
        in ("1", "true", "yes")
    )
    queue_version = (os.environ.get("QUEUE_VERSION", "v2").strip().lower() or "v2")

    slot_acquired = False
    queued_then_dispatched = False

    try:
        logger.info(
            "escalation flow START call_id=%s intent=%r queue_version=%s",
            call_id,
            intent_summary,
            queue_version,
        )

        # Step 0a — v2 (Twilio Enqueue) handoff. When QUEUE_ENABLED=true,
        # QUEUE_VERSION=v2, business hours, browser-pickup mode: the
        # customer's call leg moves out to Twilio for the entire hold +
        # dispatch flow. Cartesia speaks one announcement and returns;
        # Twilio handles the rest (hold music, position updates, press-1
        # voicemail, rep dispatch via <Dial><Queue>).
        #
        # Rollback: set QUEUE_VERSION=v1 in env → falls through to v1
        # in-Cartesia silent-hold logic below. No code redeploy needed.
        if (
            _queue_enabled()
            and queue_version == "v2"
            and browser_pickup_for_admit
            and not after_hours
        ):
            async for ev in _run_v2_queue_handoff(
                call_id=call_id,
                caller_number=caller_number,
                intent_summary=intent_summary,
                spoken_announcement=spoken_announcement,
                escalation_status=escalation_status,
            ):
                yield ev
            return

        # Step 0b — v1 in-Cartesia queue admission. Only in business
        # hours, only in browser-pickup mode (queued dispatch requires
        # the per-call conference + force-redirect path; legacy
        # cell-probe path doesn't support graceful queueing). If
        # QUEUE_ENABLED=false OR after-hours OR no-browser-pickup, the
        # existing behavior runs unchanged.
        if _queue_enabled() and browser_pickup_for_admit and not after_hours:
            if await hold_queue.try_admit():
                slot_acquired = True
                if escalation_status is not None:
                    escalation_status["phase"] = "probe_wait"
            else:
                # Queue this caller. _wait_in_queue manages the queue_wait
                # phase + yields position updates; on dispatch it flips
                # phase to "probe_wait" and returns, falling into the
                # probe code below. On hangup/timeout it yields the right
                # message itself and we return early.
                async for ev in _wait_in_queue(
                    call_id=call_id,
                    caller_number=caller_number,
                    intent_summary=intent_summary,
                    completed_flag=completed_flag,
                    escalation_status=escalation_status,
                ):
                    yield ev
                if escalation_status is None or escalation_status["phase"] != "probe_wait":
                    return  # hangup or hard-timeout — nothing more to do
                slot_acquired = True
                queued_then_dispatched = True
        else:
            if escalation_status is not None:
                escalation_status["phase"] = "probe_wait"

        # Step 1 — speak the announcement so the caller knows we heard
        # them. We removed this briefly to avoid back-to-back "connecting
        # you" messages when the LLM ALSO generated transition text
        # (Haiku's atomic-rule drift). Putting it back because the
        # silent-LLM case (atomic rule respected, OR Case 9 bypass
        # firing — no LLM involvement at all) leaves the caller hearing
        # nothing for 10s until the first filler. Silence is worse than
        # occasional redundancy. Queue-dispatch path uses a fixed
        # transition message instead of the LLM-passed announcement
        # (which would say "connecting you" right after the caller heard
        # a 5-minute queue wait — incoherent).
        announced_text = (
            QUEUE_DISPATCH_TRANSITION_MESSAGE
            if queued_then_dispatched
            else spoken_announcement
        )
        announcement_start = time.monotonic()
        yield AgentSendText(text=announced_text, interruptible=False)

        # Direct-admit probe wait: tell the caller their position so
        # every escalation has a position-aware framing (unified UX with
        # the queue-wait path). Without this they'd hear only filler
        # audio for up to 60s with no context. Skip on:
        #   - queued_then_dispatched: caller just heard "Got a rep for
        #     you now — one moment"; adding "#1 in line" would be weird.
        #   - after_hours: no live transfer happening; goes straight to
        #     callback intake.
        #   - demo_mode: demo path sleeps 5s and returns unavailable.
        # The phrasing "number 1 in line" matches the queue path's
        # spelled-out form for TTS (avoid "#" which TTS reads as
        # "pound"/"hash"; see CLAUDE.md "Spoken vs written forms").
        demo_mode = (
            os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")
        )
        if not queued_then_dispatched and not after_hours and not demo_mode:
            yield AgentSendText(
                text=(
                    "You're number 1 in line — hang tight while I get "
                    "someone for you."
                ),
                interruptible=False,
            )

        # Step 2 — fire the team Slack ping. AWAITED so it can't be
        # silently dropped (this was a bug previously when it was
        # fire-and-forget).
        browser_pickup = (
            os.environ.get("BROWSER_PICKUP", "").strip().lower()
            in ("1", "true", "yes")
        )
        conf_name = _derive_conf_name(caller_number)

        # Build the browser pickup URL the Slack button opens. Requires
        # TWILIO_FUNCTIONS_DOMAIN — if it's missing while BROWSER_PICKUP
        # is on, log loudly and fall back to the phone probe so the call
        # still gets escalated rather than silently stranding the caller.
        #
        # We also pass caller + intent through the URL so the pickup
        # page can display them. That way if the WebRTC join fails or
        # the conference is empty when the responder arrives, the
        # responder still sees who to call back and what they want.
        #
        # After-hours: no pickup URL — there's no one online to click
        # the button. The Slack ping becomes an FYI for the next-day
        # callback queue.
        pickup_url: Optional[str] = None
        if browser_pickup and not demo_mode and not after_hours:
            functions_domain = os.environ.get("TWILIO_FUNCTIONS_DOMAIN", "").strip()
            if functions_domain:
                pickup_url = (
                    f"https://{functions_domain}/agent-pickup.html"
                    f"?conf={quote(conf_name)}"
                    f"&customer={quote(caller_number)}"
                    f"&intent={quote(intent_summary[:140])}"
                )
            else:
                logger.error(
                    "BROWSER_PICKUP=true but TWILIO_FUNCTIONS_DOMAIN unset "
                    "— falling back to phone probe"
                )
                browser_pickup = False

        slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        if slack_url:
            try:
                await _send_slack_ping(
                    slack_url,
                    intent_summary,
                    caller_number,
                    demo_mode,
                    pickup_url=pickup_url,
                    after_hours=after_hours,
                )
            except Exception as e:
                logger.warning("Slack ping failed (non-fatal): %s", e)
        else:
            logger.warning("SLACK_WEBHOOK_URL not set — no team ping fired")

        # Fire a "pending" Linear ticket immediately so the team always
        # has a paper trail for this escalation, even if downstream
        # steps silently fail (LLM mid-tool hijack, Cartesia teardown,
        # etc.). The transfer-success / callback paths fire their own
        # final-outcome tickets later; duplication is intentional.
        #
        # Spawned as a pinned background task (not awaited) for two
        # reasons:
        # 1) Doesn't block the generator from reaching the probe wait.
        # 2) Survives any generator cancellation later in the flow.
        # The task internally swallows exceptions (Slack rate limit,
        # Linear outage), so backgrounding it is safe.
        #
        # Skip if we're here via the queue dispatch path — the
        # queue_waiting ticket already covered the paper-trail role
        # at queue entry time; firing escalation_pending here too
        # would create a redundant third "in progress" ticket per call.
        if not queued_then_dispatched:
            pending_task = asyncio.create_task(
                log_escalation_started(
                    call_id=call_id,
                    caller_number=caller_number,
                    intent_summary=intent_summary,
                )
            )
            _ACTIVE_PROBE_TASKS.add(pending_task)
            pending_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)

        # Step 3 — run the probe. Yield filler audio every
        # PROBE_FILLER_INTERVAL_SECONDS so the customer hears something
        # instead of dead silence while we wait for a teammate. Browser-
        # pickup mode skips the outbound cell calls — the Slack button
        # is the trigger. Both paths share the same conference-
        # participant poll as the success signal.
        #
        # After-hours: no probe at all. The team is offline so polling
        # for participants would just burn 60s. Mark unavailable so we
        # fall straight through to the callback intake speech.
        if after_hours:
            logger.info("after_hours=True — skipping probe, going to callback intake")
            available = False
        elif demo_mode:
            logger.info("DEMO_MODE: sleeping ~5s, then unavailable")
            await asyncio.sleep(5)
            available = False
        else:
            if browser_pickup:
                logger.info(
                    "BROWSER_PICKUP: waiting for responder to click "
                    "Slack button and join conf=%s",
                    conf_name,
                )
                probe_task = asyncio.create_task(
                    _wait_for_browser_pickup(
                        conf_name,
                        call_id,
                        caller_number,
                        intent_summary,
                        completed_flag,
                        escalation_status,
                    )
                )
                # Pin to module-level set so an LLM hijack that cancels
                # this generator doesn't also cancel the task. The task
                # then continues polling and fires the force-redirect
                # independently — guaranteeing the customer reaches the
                # human as soon as the human shows up.
                _ACTIVE_PROBE_TASKS.add(probe_task)
                probe_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)
            else:
                probe_task = asyncio.create_task(
                    _real_probe(call_id, caller_number)
                )
            filler_idx = 0
            while True:
                try:
                    available = await asyncio.wait_for(
                        asyncio.shield(probe_task),
                        timeout=PROBE_FILLER_INTERVAL_SECONDS,
                    )
                    break
                except asyncio.TimeoutError:
                    if filler_idx < len(PROBE_FILLERS) and not probe_task.done():
                        yield AgentSendText(
                            text=PROBE_FILLERS[filler_idx],
                            interruptible=False,
                        )
                        filler_idx += 1
                    # If we're out of fillers, just loop quietly until
                    # probe_task completes (either picks up or times out
                    # at PROBE_TIMEOUT_SECONDS).

        # Step 4 — speak the outcome.
        #
        # Bridging mechanism: we use Twilio's REST API call.update
        # (force-redirect) to move the customer's call into the
        # conference, NOT Cartesia's AgentTransferCall. AgentTransferCall
        # issues <Dial> from the customer's call leg, which Twilio's
        # voice routing has been observed to reject with "busy" when
        # the originating call came in on a toll-free number. The REST
        # API mechanism doesn't place a new outbound call — it just
        # rewrites the existing call's TwiML — so it's not subject to
        # that restriction.
        if available:
            # Wait for the announcement to finish playing before the
            # force-redirect cuts the audio. Estimate from word count
            # (~0.4s/word + 0.5s buffer, capped at 8s) so short
            # announcements don't strand the customer in silence and
            # long ones aren't cut off mid-sentence.
            word_count = len(announced_text.split())
            if queued_then_dispatched:
                # Larger budget: when dispatched from queue, the LLM
                # may have been mid-FAQ-answer when the transition
                # message fired (queue_wait allows conversational
                # hold). The transition message queues behind the
                # in-flight LLM speech in Cartesia's TTS buffer. Give
                # Cartesia time to drain that buffer + play the
                # transition + leave a beat before the force-redirect
                # cuts audio. Crude but cheap; the alternative is to
                # actively cancel in-flight LLM speech which the Line
                # SDK doesn't cleanly expose.
                announcement_budget = 12.0
            else:
                announcement_budget = min(8.0, 0.4 * word_count + 0.5)
            elapsed = time.monotonic() - announcement_start
            remaining = announcement_budget - elapsed
            if remaining > 0:
                logger.info(
                    "Waiting %.1fs for announcement (%d words) to finish",
                    remaining,
                    word_count,
                )
                await asyncio.sleep(remaining)

            # Mark that primary attempted, so the delayed-backup task
            # spawned by _wait_for_browser_pickup skips its own attempt.
            if escalation_status is not None:
                escalation_status["redirect_attempted"] = True

            twilio_sid = _env("TWILIO_ACCOUNT_SID")
            twilio_token = _env("TWILIO_AUTH_TOKEN")
            client = Client(twilio_sid, twilio_token)
            success = await _force_redirect_to_conference(
                client, call_id, conf_name
            )

            if success:
                if completed_flag is not None:
                    completed_flag[0] = True
                # Fire log_call_complete as a pinned background task,
                # NOT an await. Reason: force-redirect just moved the
                # customer's call out of Cartesia. Within ~1s Cartesia
                # will close the WebSocket and cancel this generator.
                # If we awaited log_call_complete here, the cancellation
                # would interrupt it mid-flight — Linear ticket gets
                # created (first await completes) but Slack DM never
                # fires (second await cancelled). Backgrounding it
                # ensures both run to completion.
                log_task = asyncio.create_task(
                    log_call_complete(
                        call_id=call_id,
                        caller_number=caller_number,
                        caller_name=None,
                        intent_summary=intent_summary,
                        outcome="transferred",
                        recap=(
                            f"Caller bridged to human via REST-API "
                            f"force-redirect. Intent: {intent_summary}"
                        ),
                    )
                )
                _ACTIVE_PROBE_TASKS.add(log_task)
                log_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)
            else:
                # Redirect failed (call already ended, Twilio outage,
                # weird call state). Fall through to the callback-intake
                # flow so the caller still has a path forward.
                logger.warning(
                    "Primary force-redirect failed — falling back to "
                    "callback intake"
                )
                async for ev in _yield_callback_intake_message():
                    yield ev
        else:
            # Probe timed out (in-hours) OR after-hours short-circuit.
            # Either way, run callback intake. Pick the phrasing to
            # match the situation — "lines are busy" makes no sense
            # at 11pm.
            async for ev in _yield_callback_intake_message(after_hours=after_hours):
                yield ev
    finally:
        # Always reset phase to "idle", even if the generator is cancelled
        # mid-flow by an LLM hijack. This way end_call_with_goodbye
        # firing after a hijack-and-give-up correctly sees "not in
        # escalation anymore" and ends normally.
        if escalation_status is not None:
            escalation_status["phase"] = "idle"
        if slot_acquired:
            # Match the increment from try_admit() or wait_for_dispatch()
            # so the queue's MAX_CONCURRENT_REPS bookkeeping stays
            # accurate. release_probe_slot is idempotent in spirit (a
            # second decrement would just floor at 0) but should match
            # exactly one increment per call.
            await hold_queue.release_probe_slot()


def make_escalate_tool(
    call_request: CallRequest,
    completed_flag=None,
    escalation_status=None,
    after_hours: bool = False,
):
    """Build the escalate_to_human tool bound to this call's CallRequest.

    `completed_flag` is the same single-element list shared with
    end_call_with_goodbye and the CallEnded wrapper in main.get_agent.
    The transfer-success path here logs to Linear and then sets the
    flag (Twilio REST API call.update moves the customer's call to the
    conference, which causes Cartesia to see CallEnded shortly after) —
    setting the flag prevents the wrapper from double-logging an
    "abandoned" ticket.

    `escalation_status` is an optional dict with keys:
      - "phase": string state, one of "idle" / "probe_wait" (queue work
        adds "queue_wait" and "dispatched" later). Non-"idle" means an
        escalation is active. Used by end_call_with_goodbye (Case 8
        recovery) and record_followup (skip the callback flow if the
        LLM hijacks mid-wait) and by main.py's wrapper (Case 9
        no-op-on-duplicate check).
      - "redirect_attempted": set True when the inline force-redirect
        is about to fire. The delayed-backup task in
        _wait_for_browser_pickup checks this before attempting its own
        redirect, so the backup only kicks in when the inline path was
        never reached (LLM-hijack scenario).

    `after_hours` is captured at call start (from main.get_agent's
    business-hours check) and threaded into run_escalation_flow so the
    same factory output behaves correctly for the whole call.
    """

    caller_number = call_request.from_ or "unknown"
    call_id = call_request.call_id

    @passthrough_tool
    async def escalate_to_human(
        ctx,
        spoken_announcement: Annotated[
            str,
            "The exact short sentence to speak to the caller BEFORE we "
            "reach out to the team. Match the wording to the trigger. "
            "Examples: 'Yeah, that's account-specific so I'd want to get "
            "someone on our team — give me one moment to reach out.' / "
            "'I can't give personal advice on this call, but let me try "
            "grabbing someone on our team — hang on one moment.' / "
            "'Of course — let me try reaching our team for you. One "
            "moment.' / 'Hmm, that's not something I can answer myself, "
            "but our team can — let me try them. Hang on one moment.'",
        ],
        intent_summary: Annotated[
            str,
            "One sentence, in the caller's own words, describing what "
            "they want. Used in the Slack notification to the team.",
        ],
    ):
        """Reach out to the team for help with this caller. Handles
        everything end-to-end: speaks an announcement, pings the team via
        Slack with a "Take call in browser" button, polls the conference
        for a rep, and either bridges the caller in (force-redirect via
        Twilio REST API) or speaks the lines-busy fallback.

        After this tool finishes, the caller has either:
          - Been moved into the conference with a human (call leg
            redirected via REST API). The bot is out of the loop.
          - Heard "Sorry, all our lines are busy — what's your full
            name?" — your next job is to collect the caller's name and
            callback number, then call record_followup. The unavailable
            speech already asked for the name, so don't re-ask — just
            take the response and proceed to the number.
        """
        # No-op if an escalation is already running (race with the Case
        # 9 wrapper trigger, or the LLM calling this tool twice in a
        # row). The active one continues; this call returns immediately.
        if escalation_status is not None and escalation_status.get("phase", "idle") != "idle":
            logger.info(
                "escalate_to_human called but escalation already in progress — "
                "skipping duplicate"
            )
            return

        async for ev in run_escalation_flow(
            call_id=call_id,
            caller_number=caller_number,
            spoken_announcement=spoken_announcement,
            intent_summary=intent_summary,
            completed_flag=completed_flag,
            escalation_status=escalation_status,
            after_hours=after_hours,
        ):
            yield ev

    return escalate_to_human


# === Browser-pickup probe ====================================================


async def _wait_for_browser_pickup(
    conf_name: str,
    call_id: str,
    caller_number: str,
    intent_summary: str,
    completed_flag,
    escalation_status,
) -> bool:
    """Browser-pickup mode: skip placing outbound cell calls and just
    wait for the Slack-button responder to join the conference via the
    Twilio Voice JS SDK.

    On success, also schedules a delayed-backup force-redirect task so
    that an LLM-hijack-induced generator cancel (which would prevent the
    inline force-redirect in run_escalation_flow from running) still
    delivers the customer to the conference. The backup checks
    escalation_status["redirect_attempted"] first — if the inline path
    already tried (success or failure), the backup skips so we don't
    yank the caller out of the callback-intake flow on a primary
    failure.

    Returns True if someone joins within PROBE_TIMEOUT_SECONDS.
    """
    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    client = Client(twilio_sid, twilio_token)
    joined = await _wait_for_participant(client, conf_name, _probe_timeout_seconds())
    if joined:
        redirect_task = asyncio.create_task(
            _delayed_force_redirect(
                client,
                call_id,
                conf_name,
                caller_number,
                intent_summary,
                completed_flag,
                escalation_status,
                # Give the inline primary in run_escalation_flow time to
                # both speak the "connecting you" line (~3.5s) and fire
                # the REST API redirect. Backup fires after that window.
                delay_seconds=8,
            )
        )
        # Pin so an LLM-hijack-induced generator cancel doesn't kill it.
        _ACTIVE_PROBE_TASKS.add(redirect_task)
        redirect_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)
    return joined


async def _delayed_force_redirect(
    client,
    call_id: str,
    conf_name: str,
    caller_number: str,
    intent_summary: str,
    completed_flag,
    escalation_status,
    delay_seconds: float,
) -> None:
    """Belt-and-suspenders backup for the inline force-redirect in
    run_escalation_flow. Only fires if the inline path was never
    attempted (LLM-hijack scenario where the generator was cancelled
    before reaching it).
    """
    await asyncio.sleep(delay_seconds)

    if escalation_status is not None and escalation_status.get("redirect_attempted"):
        logger.info(
            "Backup force-redirect skipped — primary already attempted"
        )
        return
    if completed_flag is not None and completed_flag[0]:
        logger.info(
            "Backup force-redirect skipped — call already completed"
        )
        return

    success = await _force_redirect_to_conference(client, call_id, conf_name)
    if not success:
        return

    try:
        await log_call_complete(
            call_id=call_id,
            caller_number=caller_number,
            caller_name=None,
            intent_summary=intent_summary,
            outcome="transferred",
            recap=(
                f"Caller bridged via backup force-redirect (LLM hijack "
                f"path — inline primary never ran). Intent: {intent_summary}"
            ),
        )
    except Exception as e:
        logger.warning(
            "Backup-redirect transferred-ticket log failed (non-fatal): %s", e
        )
    if completed_flag is not None:
        completed_flag[0] = True


async def _redirect_to_queue(call_id: str, caller_number: str, intent_summary: str) -> bool:
    """v2 entry: Twilio REST `call.update` to replace the customer's
    call-leg TwiML with the <Enqueue> response from /enqueue-customer.
    After this fires, the customer's call leg is in Twilio's queue
    (hearing hold music + position updates); Cartesia's WebSocket closes
    within ~1s. From that point, the /queue-action Function is
    authoritative for the outcome ticket — main.py's CallEnded handler
    sees phase=queue_handoff and suppresses its own abandoned ticket.

    Returns True on REST success, False on failure. The caller falls
    through to in-Cartesia callback intake on failure (preserves the
    "no caller asking for human ends call without team notification"
    invariant).
    """
    functions_domain = os.environ.get("TWILIO_FUNCTIONS_DOMAIN", "").strip()
    if not functions_domain:
        logger.warning("TWILIO_FUNCTIONS_DOMAIN unset — can't enqueue via REST")
        return False

    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    twilio_call_sid = call_id[3:] if call_id.startswith("ac_") else call_id
    params = "&".join([
        f"call_id={quote(call_id)}",
        f"caller={quote(caller_number)}",
        f"intent={quote(intent_summary[:200])}",
    ])
    enqueue_url = f"https://{functions_domain}/enqueue-customer?{params}"

    client = Client(twilio_sid, twilio_token)
    try:
        await asyncio.to_thread(
            client.calls(twilio_call_sid).update,
            method="POST",
            url=enqueue_url,
        )
        logger.info(
            "v2 enqueue redirect: call=%s -> /enqueue-customer (queue=bc-support)",
            twilio_call_sid,
        )
        return True
    except Exception as e:
        logger.warning(
            "v2 enqueue redirect FAILED (non-fatal — falling through to callback intake): %s",
            e,
        )
        return False


async def _run_v2_queue_handoff(
    *,
    call_id: str,
    caller_number: str,
    intent_summary: str,
    spoken_announcement: str,
    escalation_status,
):
    """v2 queue path: speak one announcement, post Slack ping with
    `?mode=queue` URL, fire the escalation_pending Linear ticket, then
    REST-update the customer's call leg into the Twilio queue. After the
    REST call returns success, Cartesia's session ends (the customer's
    call is now in Twilio). We `return` here; main.py's CallEnded
    handler sees `phase == "queue_handoff"` and suppresses the abandoned
    ticket so /queue-action can own the final outcome.

    On REST failure (Twilio outage, dead call, etc.), yield the
    callback-intake message and return — same fallback as v1's
    probe-failure path. Phase stays at queue_handoff during fallback;
    main.py CallEnded will still suppress, but the LLM intake flow
    will set phase=idle once record_followup fires.
    """
    if escalation_status is not None:
        escalation_status["phase"] = "queue_handoff"

    # Step 1 — hardcoded transfer announcement. We INTENTIONALLY ignore
    # the LLM-supplied `spoken_announcement` here because Haiku's
    # wording varies ("Sure — one moment" / "Let me check..." / etc.)
    # and the caller needs unambiguous "you're being transferred"
    # framing right before the silence + music kicks in. The
    # hardcoded text also names the hold music explicitly so the
    # transition feels deliberate, not like a dropped call.
    yield AgentSendText(text=V2_TRANSFER_ANNOUNCEMENT, interruptible=False)

    # Step 2 — Slack ping with the v2 pickup URL (?mode=queue). Button
    # opens agent-pickup.html in queue mode → click Join → TwiML App
    # invokes /agent-dial with mode=queue → <Dial><Queue> bridges to
    # the head-of-queue caller. FIFO + atomicity provided by Twilio.
    functions_domain = os.environ.get("TWILIO_FUNCTIONS_DOMAIN", "").strip()
    pickup_url: Optional[str] = None
    if functions_domain:
        pickup_url = (
            f"https://{functions_domain}/agent-pickup.html"
            f"?mode=queue"
            f"&customer={quote(caller_number)}"
            f"&intent={quote(intent_summary[:140])}"
        )
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        try:
            await _send_slack_ping(
                slack_url,
                intent_summary,
                caller_number,
                demo_mode=False,
                pickup_url=pickup_url,
                after_hours=False,
            )
        except Exception as e:
            logger.warning("v2 Slack ping failed (non-fatal): %s", e)

    # Step 3 — Linear escalation_pending ticket. Pinned background task
    # so generator cancellation (which fires soon after the REST update)
    # doesn't drop the ticket.
    pending_task = asyncio.create_task(
        log_escalation_started(
            call_id=call_id,
            caller_number=caller_number,
            intent_summary=intent_summary,
        )
    )
    _ACTIVE_PROBE_TASKS.add(pending_task)
    pending_task.add_done_callback(_ACTIVE_PROBE_TASKS.discard)

    # Step 4 — let the announcement play before we redirect. The
    # V2_TRANSFER_ANNOUNCEMENT is 14 words (~5s of Cartesia TTS); we
    # sleep just long enough for Cartesia to render most of it. Set
    # short so the music starts ASAP after the announcement, minimizing
    # the silent gap between Cartesia and Twilio (which previously felt
    # like a dropped call to testers). The Twilio /enqueue-customer
    # pre-queue Say covers any residual cut-off if the budget is too
    # short.
    await asyncio.sleep(3.0)

    # Step 5 — REST call.update. On success, Cartesia session ends
    # shortly; on failure, fall through to in-Cartesia callback intake.
    ok = await _redirect_to_queue(call_id, caller_number, intent_summary)
    if ok:
        logger.info("v2 handoff complete — Twilio now owns call_id=%s", call_id)
        return

    logger.error(
        "v2 redirect failed — falling through to in-Cartesia callback intake call_id=%s",
        call_id,
    )
    if escalation_status is not None:
        # Drop back to probe-style phase so the in-Cartesia callback
        # intake flow runs cleanly (record_followup's phase==idle gate
        # will pass once the LLM completes intake and phase is reset).
        escalation_status["phase"] = "probe_wait"
    async for ev in _yield_callback_intake_message():
        yield ev


async def _force_redirect_to_conference(client, call_id: str, conf_name: str) -> bool:
    """Use Twilio's REST API to move the customer's call leg into the
    conference, bypassing Cartesia. Returns True on success, False
    otherwise. The caller uses the result to decide whether to also
    write the transferred-outcome Linear ticket.
    """
    functions_domain = os.environ.get("TWILIO_FUNCTIONS_DOMAIN", "").strip()
    if not functions_domain:
        logger.warning(
            "TWILIO_FUNCTIONS_DOMAIN unset — can't issue force-redirect"
        )
        return False

    # call_id from CallRequest may or may not include Cartesia's "ac_"
    # prefix; Twilio's REST API needs the raw CallSid (CAxxxx...).
    twilio_call_sid = call_id[3:] if call_id.startswith("ac_") else call_id
    redirect_url = (
        f"https://{functions_domain}/conference-join?conf={quote(conf_name)}"
    )
    try:
        await asyncio.to_thread(
            client.calls(twilio_call_sid).update,
            method="POST",
            url=redirect_url,
        )
        logger.info(
            "Force-redirect: call=%s -> conf=%s", twilio_call_sid, conf_name
        )
        return True
    except Exception as e:
        logger.warning(
            "Force-redirect failed (non-fatal, call may already be moved): %s",
            e,
        )
        return False


# === Real-mode probe (Twilio conference) ====================================


async def _real_probe(call_id: str, caller_number: str) -> bool:
    """Place outbound probe calls and poll the conference for participants.
    Returns True if a human joined the conference, False otherwise.

    Conference name is derived from the caller's number so simultaneous
    calls from different numbers don't share a room — see
    `_derive_conf_name` for details. The Twilio Function on the
    customer's transfer-in leg derives the same name from event.From.
    """
    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    from_number = _env("TWILIO_FROM_NUMBER")
    conf_name = _derive_conf_name(caller_number)

    cells = [
        c
        for c in (os.environ.get("TAYLOR_CELL"), os.environ.get("ARYAMAAN_CELL"))
        if c
    ]
    if not cells:
        logger.error("No hunt-group cells configured — falling through")
        return False

    client = Client(twilio_sid, twilio_token)
    probe_twiml = _build_probe_twiml(conf_name)

    try:
        call_sids = await asyncio.gather(
            *[
                asyncio.to_thread(
                    _place_probe_call, client, cell, from_number, probe_twiml
                )
                for cell in cells
            ]
        )
    except Exception as e:
        logger.exception("Failed to place probe calls: %s", e)
        return False

    logger.info("Probe calls placed for call_id=%s", call_id)
    joined = await _wait_for_participant(client, conf_name, _probe_timeout_seconds())

    if not joined:
        # Cancel ringing legs so we don't keep ringing after the caller
        # has moved on to the ticket flow.
        await asyncio.gather(
            *[asyncio.to_thread(_cancel_call, client, sid) for sid in call_sids if sid],
            return_exceptions=True,
        )
        return False

    return True


def _build_probe_twiml(conf_name: str) -> str:
    """TwiML played on the responder's leg when the probe places an
    outbound call to their cell.

    With TWILIO_FUNCTIONS_DOMAIN set: plays "Press 1 to accept" and
    routes <Gather> at the /probe-accept Function. Voicemails can't
    press digits, so this gate filters them out cleanly — only a live
    responder who confirms by pressing 1 actually joins the conference.

    Without the env var (or before /probe-accept is deployed): falls
    back to direct conference join (no press-1 gate). Loses voicemail
    filtering but keeps the normal flow working. Safe to roll back.

    Slack already pings the responder with intent context before they
    pick up, so the responder knows what the call is about even with
    the press-1 prompt being terse.
    """
    functions_domain = os.environ.get("TWILIO_FUNCTIONS_DOMAIN", "").strip()
    if not functions_domain:
        logger.warning(
            "TWILIO_FUNCTIONS_DOMAIN unset — probe will drop responders "
            "directly into the conference (no press-1 voicemail filter)"
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Conference startConferenceOnEnter="true" endConferenceOnExit="true" beep="false">{conf_name}</Conference>
  </Dial>
</Response>"""

    accept_url = (
        f"https://{functions_domain}/probe-accept?conf={quote(conf_name)}"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather numDigits="1" timeout="10" action="{accept_url}" method="POST">
    <Say voice="Polly.Joanna">You have an incoming Basic Capital call. Press 1 to accept.</Say>
  </Gather>
  <Hangup/>
</Response>"""


def _place_probe_call(client, to_number: str, from_number: str, twiml: str) -> Optional[str]:
    try:
        call = client.calls.create(to=to_number, from_=from_number, twiml=twiml)
        return call.sid
    except Exception as e:
        logger.warning("Failed to place probe call to %s: %s", to_number, e)
        return None


async def _wait_for_participant(client, conf_name: str, timeout_s: int) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            participants = await asyncio.to_thread(_list_participants, client, conf_name)
            if participants:
                return True
        except Exception as e:
            # Log loudly: if Twilio auth breaks or the API regresses, every
            # probe will silently time out and you'd never see why. INFO
            # logger isn't enough — needs to be visible.
            logger.warning(
                "Twilio participant poll failed (will retry): %s", e
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return False


def _list_participants(client, conf_name: str) -> list:
    confs = list(
        client.conferences.list(friendly_name=conf_name, status="in-progress", limit=5)
    )
    if not confs:
        return []
    conf = confs[0]
    return list(client.conferences(conf.sid).participants.list(limit=5))


def _cancel_call(client, sid: str) -> None:
    try:
        client.calls(sid).update(status="canceled")
    except Exception as e:
        logger.debug("Could not cancel call %s: %s", sid, e)


# === Slack call-ping =========================================================


async def _send_slack_ping(
    webhook_url: str,
    intent_summary: str,
    caller_number: str,
    demo_mode: bool,
    *,
    pickup_url: Optional[str] = None,
    after_hours: bool = False,
) -> None:
    """Notify the team that a caller is being escalated.

    If pickup_url is provided (BROWSER_PICKUP mode, in business hours),
    the message renders as Block Kit with a primary "Take call in
    browser" button that opens the per-call pickup page. After-hours
    pings are FYI-only — no button (no one to click it) and phrasing
    that flags the callback ticket will land in Linear for the next
    business day.
    """
    if after_hours:
        payload = {
            "text": (
                f":telephone_receiver: *After-hours Basic Capital call*\n"
                f"*Caller:* {caller_number}\n"
                f"*Wants:* {intent_summary}\n"
                f"_FYI — caller is being led into callback intake. A "
                f"Linear ticket with their name and number will follow._"
            )
        }
    elif pickup_url:
        payload = {
            "text": f":telephone_receiver: Incoming BC call from {caller_number}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":telephone_receiver: *Incoming Basic Capital call*\n"
                            f"*Caller:* {caller_number}\n"
                            f"*Wants:* {intent_summary}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "style": "primary",
                            "text": {
                                "type": "plain_text",
                                "text": "Take call in browser",
                            },
                            "url": pickup_url,
                        }
                    ],
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                "_First to click joins the conference. "
                                "Falls back to callback intake if nobody clicks in ~60s._"
                            ),
                        }
                    ],
                },
            ],
        }
    elif demo_mode:
        payload = {
            "text": (
                f":telephone_receiver: *Bot escalated (DEMO mode)*\n"
                f"*Caller:* {caller_number}\n"
                f"*Wants:* {intent_summary}\n"
                f"_No real probe — caller will be led into callback intake._"
            )
        }
    else:
        payload = {
            "text": (
                f":telephone_receiver: *Incoming Basic Capital call*\n"
                f"*Caller:* {caller_number}\n"
                f"*Wants:* {intent_summary}\n"
                f"_Phones ringing now — answer to be placed in the conference._"
            )
        }
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(webhook_url, json=payload)
    resp.raise_for_status()
    logger.info("Slack ping sent (status=%s)", resp.status_code)
