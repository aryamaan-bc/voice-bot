"""Escalate the call to a human via a Twilio conference-based probe.

Flow:
  1. Slack ping to #bc-support with intent + caller number.
  2. Outbound Twilio calls to Taylor and Aryamaan with inline TwiML that
     announces the intent and drops them into a shared conference room.
     Whoever answers first "wins" — they're already in the room, waiting.
  3. Poll the Twilio Conference API for up to ~25s to see if anyone joined.
  4. If joined → return 'available: <conf_name>' (the LLM then calls
     transfer_call, which bridges the caller into the same conference —
     no double-ring).
  5. If nobody joined → end the pending outbound legs, return 'unavailable'
     (the LLM then proceeds to create_callback_ticket).

Exposed as a factory (`make_escalate_tool(call_request)`) so the tool can
close over the caller's number from the incoming CallRequest — Line's tool
ctx is empty and doesn't carry call metadata.
"""

import asyncio
import logging
import os
from typing import Annotated, Optional

import httpx
from line.llm_agent.tools.decorators import loopback_tool
from line.voice_agent_app import CallRequest
from twilio.rest import Client

logger = logging.getLogger(__name__)


# === Env-driven config (read lazily to avoid import-time failures) =========
def _env(name: str, *, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


PROBE_TIMEOUT_SECONDS = 25  # upper bound on how long we wait for someone to answer
POLL_INTERVAL_SECONDS = 1

# Keep fire-and-forget tasks alive (asyncio GC can collect orphan tasks).
_pending_bg_tasks: set[asyncio.Task] = set()


def make_escalate_tool(call_request: CallRequest):
    """Build the escalate_to_human tool bound to this call's CallRequest."""

    caller_number = call_request.from_ or "unknown"
    call_id = call_request.call_id

    @loopback_tool
    async def escalate_to_human(
        ctx,
        intent_summary: Annotated[
            str,
            "One sentence, in the caller's own words, describing what they "
            "want. Example: 'wants to know if she can roll over a SEP-IRA "
            "from Fidelity.'",
        ],
    ) -> str:
        """Try to reach Taylor or Aryamaan via a conference-based probe.

        Returns either:
          - "available: <conference_name>" — someone answered and is waiting
            in the conference. You should immediately say a short line like
            "Connecting you now" and then call transfer_call with the
            CONFERENCE_JOIN_NUMBER (from env) as the target — that bridges
            the caller into the conference.
          - "unavailable" — nobody picked up. Proceed to ask the caller for
            a callback number and call create_callback_ticket.
        """
        # Demo mode: skip Twilio entirely, simulate "nobody answered" so the
        # flow exercises the email-fallback branch. Lets us run `cartesia
        # chat` in a browser with zero telephony infrastructure.
        if os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes"):
            logger.info(
                "DEMO_MODE: simulating probe for call_id=%s (intent=%r) — "
                "sleeping briefly then returning unavailable",
                call_id,
                intent_summary,
            )
            await asyncio.sleep(2)
            return "unavailable"

        twilio_sid = _env("TWILIO_ACCOUNT_SID")
        twilio_token = _env("TWILIO_AUTH_TOKEN")
        from_number = _env("TWILIO_FROM_NUMBER")
        conf_name = os.environ.get("CONFERENCE_NAME", "bc-active")
        slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")

        cells = [c for c in (os.environ.get("TAYLOR_CELL"), os.environ.get("ARYAMAAN_CELL")) if c]
        if not cells:
            logger.error("No hunt-group cells configured (TAYLOR_CELL / ARYAMAAN_CELL)")
            return "unavailable"

        client = Client(twilio_sid, twilio_token)

        # Fire Slack ping in the background — don't block the probe on it.
        if slack_url:
            task = asyncio.create_task(
                _send_slack_ping(slack_url, intent_summary, caller_number, conf_name)
            )
            _pending_bg_tasks.add(task)
            task.add_done_callback(_pending_bg_tasks.discard)

        probe_twiml = _build_probe_twiml(intent_summary, conf_name)

        # Place the outbound probe calls. Run the sync Twilio SDK in a thread
        # pool so we don't block the event loop.
        try:
            call_sids = await asyncio.gather(
                *[
                    asyncio.to_thread(
                        _place_probe_call,
                        client,
                        cell,
                        from_number,
                        probe_twiml,
                    )
                    for cell in cells
                ]
            )
        except Exception as e:
            logger.exception("Failed to place probe calls: %s", e)
            return "unavailable"

        logger.info(
            "Probe calls placed for call_id=%s: %s", call_id, dict(zip(cells, call_sids))
        )

        # Poll the conference for participants.
        joined = await _wait_for_participant(client, conf_name, PROBE_TIMEOUT_SECONDS)

        if joined:
            logger.info("Probe succeeded for call_id=%s — human in %s", call_id, conf_name)
            return f"available: {conf_name}"

        # Nobody answered — cancel the ringing outbound legs so we don't keep
        # ringing their phones after the caller has moved on to the ticket flow.
        await asyncio.gather(
            *[asyncio.to_thread(_cancel_call, client, sid) for sid in call_sids if sid],
            return_exceptions=True,
        )
        logger.info("Probe timed out for call_id=%s", call_id)
        return "unavailable"

    return escalate_to_human


# ── helpers ────────────────────────────────────────────────────────────────


def _build_probe_twiml(intent_summary: str, conf_name: str) -> str:
    """Inline TwiML for the outbound probe. Announces the call, then drops
    the answerer into the conference. endConferenceOnExit=false on the
    human side means the conference survives if they disconnect first;
    startConferenceOnEnter=true so the conference is live as soon as they
    join (the caller-side joiner uses startConferenceOnEnter=true too)."""
    # Basic XML escaping for the intent summary.
    safe_intent = (
        intent_summary.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna">Call from Basic Capital. Caller wants to know: {safe_intent}. Connecting you now.</Say>
  <Dial>
    <Conference startConferenceOnEnter="true" endConferenceOnExit="true" beep="false">{conf_name}</Conference>
  </Dial>
</Response>"""


def _place_probe_call(client, to_number: str, from_number: str, twiml: str) -> Optional[str]:
    """Place one outbound probe call. Returns the Call SID, or None on failure."""
    try:
        call = client.calls.create(to=to_number, from_=from_number, twiml=twiml)
        return call.sid
    except Exception as e:
        logger.warning("Failed to place probe call to %s: %s", to_number, e)
        return None


async def _wait_for_participant(client, conf_name: str, timeout_s: int) -> bool:
    """Poll the Twilio Conference for participants; return True as soon as
    the conference has at least one active participant."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            participants = await asyncio.to_thread(_list_participants, client, conf_name)
            if participants:
                return True
        except Exception as e:
            # Transient: conference may not exist yet until someone joins.
            logger.debug("Participant poll error (ignorable): %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return False


def _list_participants(client, conf_name: str) -> list:
    """Return active participants in the named conference. Twilio resolves
    conferences by FriendlyName; filter to in-progress ones only."""
    confs = list(client.conferences.list(friendly_name=conf_name, status="in-progress", limit=5))
    if not confs:
        return []
    # Grab the most recent in-progress conference with this name.
    conf = confs[0]
    return list(client.conferences(conf.sid).participants.list(limit=5))


def _cancel_call(client, sid: str) -> None:
    """Best-effort cancel of a ringing outbound leg."""
    try:
        client.calls(sid).update(status="canceled")
    except Exception as e:
        logger.debug("Could not cancel call %s: %s", sid, e)


async def _send_slack_ping(
    webhook_url: str, intent_summary: str, caller_number: str, conf_name: str
) -> None:
    message = {
        "text": (
            f":telephone_receiver: *Incoming Basic Capital call*\n"
            f"*Caller:* {caller_number}\n"
            f"*Wants:* {intent_summary}\n"
            f"*Conference:* `{conf_name}` — your phone's ringing now. "
            f"Answer to be placed in the room; auto-falls to ticket in "
            f"~{PROBE_TIMEOUT_SECONDS}s."
        )
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(webhook_url, json=message)
    except Exception as e:
        # Slack is best-effort; never let it block the call.
        logger.warning("Slack ping failed: %s", e)
