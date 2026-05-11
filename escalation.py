"""Escalate the call to a human via a single passthrough tool.

The tool handles the entire escalation flow itself:
  1. Speaks an announcement to the caller (no LLM-text-before-tool race).
  2. Fires a Slack call-ping to the team.
  3. Runs the probe (real Twilio in production; 5s sleep in demo mode).
  4. Speaks the outcome:
       - Available: "Connecting you now" + AgentTransferCall.
       - Unavailable: "Team is tied up — would you like a callback or to
         email us?" Then the LLM handles the next steps (gathering name/
         number, calling record_followup).

Why passthrough instead of loopback:
  Gemini 2.5 Flash unreliably interleaves text generation with tool calls,
  and unreliably calls multiple tools in sequence. Putting all the speech
  INSIDE the tool — yielding AgentSendText events directly — sidesteps the
  model's tool-calling quirks entirely.
"""

import asyncio
import logging
import os
import time
from typing import Annotated, Optional

import httpx
from line.events import AgentSendText, AgentTransferCall
from line.llm_agent.tools.decorators import passthrough_tool
from line.voice_agent_app import CallRequest
from twilio.rest import Client

from linear_ticket import log_call_complete

logger = logging.getLogger(__name__)


PROBE_TIMEOUT_SECONDS = 40  # upper bound on how long we wait for someone to answer
POLL_INTERVAL_SECONDS = 1

# Filler audio played to the customer at ~10s intervals during the probe
# wait, so they don't sit in 10+ seconds of pure silence and assume the
# call dropped. Spoken in order; varied wording so it doesn't sound like
# a loop. The list covers the full PROBE_TIMEOUT_SECONDS window — first
# filler addresses "are you still there?" head-on so callers don't have
# to ask.
PROBE_FILLER_INTERVAL_SECONDS = 10
PROBE_FILLERS = [
    "Yep, still here — just reaching out to our team. One moment.",
    "Bear with me — should just be another second.",
    "Hang tight, almost there.",
    "Really appreciate your patience — almost set.",
]

# Spoken on both the probe-failed path AND the misconfigured-target path
# (probe succeeded but CONFERENCE_JOIN_NUMBER is empty — no destination
# to transfer to). The LLM then handles the callback/email choice.
TEAM_UNAVAILABLE_MESSAGE = (
    "Looks like our team is tied up right now. I can take "
    "your name and a callback number to have someone follow "
    "up within one business day, or you can email support "
    "at basic capital dot com if that's easier — which "
    "works better for you?"
)


def _env(name: str, *, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def make_escalate_tool(call_request: CallRequest, completed_flag=None):
    """Build the escalate_to_human tool bound to this call's CallRequest.

    `completed_flag` is the same single-element list shared with
    end_call_with_goodbye and the CallEnded wrapper in main.get_agent.
    The transfer-success path here logs to Linear and then ends the call
    via AgentTransferCall, which fires CallEnded — set the flag so the
    wrapper doesn't double-log an "abandoned" ticket.
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
        Slack, runs the probe, and speaks the outcome to the caller.

        After this tool finishes, the caller has been told either:
          - The call is being transferred (and AgentTransferCall has fired)
          - The team is tied up — would they prefer callback or email?

        For the unavailable path, your next job is to wait for the
        caller's response, ask for their name (and phone number if they
        chose callback), then call record_followup.
        """
        logger.info(
            "escalate_to_human START call_id=%s intent=%r", call_id, intent_summary
        )

        # Step 1 — announce to the caller. interruptible=False so the
        # caller hears the full sentence before the silent probe pause.
        yield AgentSendText(text=spoken_announcement, interruptible=False)

        # Step 2 — fire the team Slack ping. AWAITED so it can't be silently
        # dropped (this was a bug previously when it was fire-and-forget).
        demo_mode = (
            os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")
        )
        slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        if slack_url:
            try:
                await _send_slack_ping(
                    slack_url, intent_summary, caller_number, demo_mode
                )
            except Exception as e:
                logger.warning("Slack ping failed (non-fatal): %s", e)
        else:
            logger.warning("SLACK_WEBHOOK_URL not set — no team ping fired")

        # Step 3 — run the probe. Yield filler audio every
        # PROBE_FILLER_INTERVAL_SECONDS so the customer hears something
        # instead of dead silence while we wait for a teammate to pick up.
        if demo_mode:
            logger.info("DEMO_MODE: sleeping ~5s, then unavailable")
            await asyncio.sleep(5)
            available = False
        else:
            probe_task = asyncio.create_task(_real_probe(call_id))
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
        # Important ordering: check the transfer target BEFORE logging
        # the "transferred" Linear ticket or setting completed_flag. If
        # CONFERENCE_JOIN_NUMBER is unset (misconfig), the probe succeeded
        # but we have nowhere to send the caller — we must fall back to
        # the ticket flow WITHOUT polluting Linear with a fake transfer
        # ticket and WITHOUT blinding the abandoned-call wrapper.
        target = os.environ.get("CONFERENCE_JOIN_NUMBER", "") if available else ""

        if available and target:
            # Real transfer: log first (the transfer kills our agent leg),
            # mark complete (so the CallEnded wrapper doesn't double-log),
            # announce, then transfer.
            await log_call_complete(
                call_id=call_id,
                caller_number=caller_number,
                caller_name=None,  # we don't ask before transferring
                intent_summary=intent_summary,
                outcome="transferred",
                recap=(
                    f"Caller was bridged to a human after escalation. "
                    f"Intent: {intent_summary}"
                ),
            )
            if completed_flag is not None:
                completed_flag[0] = True
            yield AgentSendText(
                text="Looks like someone's available — connecting you now.",
                interruptible=False,
            )
            yield AgentTransferCall(target_phone_number=target)
        else:
            # Two cases land here:
            #   (a) Probe failed (no one picked up within timeout)
            #   (b) Probe succeeded but CONFERENCE_JOIN_NUMBER is unset
            # Both fall through to the callback/email flow with the same
            # spoken message. The LLM's record_followup + end_call_with_goodbye
            # will produce the correct final Linear ticket — don't log
            # anything here.
            if available and not target:
                logger.error(
                    "Probe succeeded but CONFERENCE_JOIN_NUMBER unset — "
                    "can't transfer. Falling through to ticket flow."
                )
            yield AgentSendText(
                text=TEAM_UNAVAILABLE_MESSAGE,
                interruptible=False,
            )

    return escalate_to_human


# === Real-mode probe (Twilio conference) ====================================


async def _real_probe(call_id: str) -> bool:
    """Place outbound probe calls and poll the conference for participants.
    Returns True if a human joined the conference, False otherwise."""
    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    from_number = _env("TWILIO_FROM_NUMBER")
    conf_name = os.environ.get("CONFERENCE_NAME", "bc-active")

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
    joined = await _wait_for_participant(client, conf_name, PROBE_TIMEOUT_SECONDS)

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
    # No Polly preamble: it ate ~5s of join time, which pushed past
    # PROBE_TIMEOUT_SECONDS in testing. Slack already pings the responder
    # with full intent context before they pick up.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Conference startConferenceOnEnter="true" endConferenceOnExit="true" beep="false">{conf_name}</Conference>
  </Dial>
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
    webhook_url: str, intent_summary: str, caller_number: str, demo_mode: bool
) -> None:
    if demo_mode:
        body = (
            f":telephone_receiver: *Bot escalated (DEMO mode)*\n"
            f"*Caller:* {caller_number}\n"
            f"*Wants:* {intent_summary}\n"
            f"_No real probe — caller will be offered callback or email._"
        )
    else:
        body = (
            f":telephone_receiver: *Incoming Basic Capital call*\n"
            f"*Caller:* {caller_number}\n"
            f"*Wants:* {intent_summary}\n"
            f"_Phones ringing now — answer to be placed in the conference._"
        )
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(webhook_url, json={"text": body})
    resp.raise_for_status()
    logger.info("Slack ping sent (status=%s)", resp.status_code)
