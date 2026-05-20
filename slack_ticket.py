"""Post a follow-up request to Slack.

Used when escalate_to_human returns 'unavailable' — handles BOTH the
phone-callback path and the email-followup path. Posts a structured
message to a Slack incoming webhook (reuses SLACK_WEBHOOK_URL — same
channel as the incoming-call pings, just different message format).

Exposed as a factory (`make_followup_tool(call_request)`) so the tool
can embed the Cartesia call_id and inbound caller number — Line's tool
ctx is empty.
"""

import logging
import os
from typing import Annotated, Literal

import httpx
from line.llm_agent.tools.decorators import loopback_tool
from line.voice_agent_app import CallRequest

import hold_queue

logger = logging.getLogger(__name__)


def make_followup_tool(call_request: CallRequest, escalation_status=None):
    """Build the record_followup tool bound to this call's CallRequest.

    `escalation_status` is an optional dict (shared with escalate_to_human
    and end_call_with_goodbye). Behavior depends on the current `phase`:
      - "probe_wait": refuse — line isn't actually busy yet and a human
        might be about to join. Return a "stay on the line" string for
        the LLM to speak.
      - "queue_wait": treat as voluntary opt-out from the queue. Dequeue
        the caller (so the next person advances) + reset phase to "idle"
        + fall through to normal callback intake.
      - "idle": normal callback intake.
    """

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    @loopback_tool
    async def record_followup(
        ctx,
        caller_name: Annotated[
            str,
            "The caller's FULL name (first + last) as they gave it. "
            "Confirm spelling with the caller for non-Anglophone names "
            "before calling this tool. Don't make this up.",
        ],
        contact_method: Annotated[
            Literal["phone", "email"],
            "How the caller wants to be reached. Use 'phone' if they "
            "asked for a callback. Use 'email' if they said they'll "
            "email support@basiccapital.com instead.",
        ],
        intent_summary: Annotated[
            str,
            "One sentence describing what the caller wants, in their own "
            "words.",
        ],
        callback_number: Annotated[
            str,
            "The phone number to call them back at. ONLY required if "
            "contact_method='phone'. Pass an empty string '' if "
            "contact_method='email'.",
        ] = "",
    ) -> str:
        """Log a follow-up request to Slack so the team has context.

        Use this for BOTH the callback path AND the email path:
        - contact_method='phone' → Slack message titled "Callback request"
        - contact_method='email' → Slack message titled "Email expected"

        Call this BEFORE confirming to the caller, so a mid-sentence
        hangup doesn't lose the request.

        Returns a short instruction string for the LLM with the exact
        wording to use when confirming to the caller — or an email
        fallback if Slack is unreachable.
        """
        # During the probe wait, this tool fires only because the LLM
        # hijacked from the caller's mid-wait speech. The line isn't
        # actually busy and a human might be about to join — refuse to
        # enter callback intake, no Slack ticket, tell the LLM to stay
        # quiet.
        if escalation_status is not None and escalation_status.get("phase", "idle") == "probe_wait":
            logger.warning(
                "record_followup called during probe_wait "
                "(call_id=%s) — suppressing Slack ticket; LLM should "
                "return to silent hold mode",
                call_id,
            )
            return (
                "Hold on — our team is still reaching out. "
                "Stay on the line."
            )

        # During the queue wait, the caller is voluntarily opting out
        # of waiting for a rep ("just take my info instead"). Dequeue
        # them so the next person in line advances, and reset phase to
        # "idle" so downstream tools (end_call_with_goodbye, CallEnded)
        # treat this as a clean callback-intake completion rather than
        # an in-progress escalation.
        if escalation_status is not None and escalation_status.get("phase", "idle") == "queue_wait":
            await hold_queue.dequeue(call_id)
            escalation_status["phase"] = "idle"
            logger.info(
                "record_followup called during queue_wait "
                "(call_id=%s) — dequeued; phase reset to idle. "
                "Proceeding with normal callback intake.",
                call_id,
            )

        webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not webhook_url:
            logger.error("SLACK_WEBHOOK_URL not set — falling back to email")
            return _email_fallback_instruction(caller_name)

        if contact_method == "phone":
            header_text = ":telephone_receiver: Callback request"
            top_line = f":telephone_receiver: Callback requested: {caller_name} — {intent_summary}"
            contact_field = {
                "type": "mrkdwn",
                "text": f"*Callback number:*\n{callback_number or '(not given)'}",
            }
        else:  # email
            header_text = ":incoming_envelope: Email expected"
            top_line = f":incoming_envelope: Email expected from: {caller_name} — {intent_summary}"
            contact_field = {
                "type": "mrkdwn",
                "text": "*Reply via:*\nEmail to support@basiccapital.com",
            }

        message = {
            "text": top_line,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": header_text},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Name:*\n{caller_name}"},
                        contact_field,
                        {"type": "mrkdwn", "text": f"*Inbound from:*\n{caller_number}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Caller wants:* {intent_summary}",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"Cartesia call ID: `{call_id}` — "
                                "audio + transcript in Cartesia dashboard"
                            ),
                        }
                    ],
                },
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=message)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Slack post failed: %s", e)
            return _email_fallback_instruction(caller_name)

        logger.info(
            "Slack follow-up logged for call_id=%s name=%r method=%s",
            call_id,
            caller_name,
            contact_method,
        )

        # Return value is the EXACT text the bot should speak — no meta
        # prefix like "Tell the caller:" because the LLM can mis-render
        # those as actual speech. The system prompt instructs the LLM to
        # speak this string verbatim. Includes the "anything else?" close
        # so the prompt doesn't need to add it separately (which caused
        # double-asks).
        if contact_method == "phone":
            return (
                f"Got it, {caller_name} — someone from our team will "
                f"follow up at {callback_number} within one business day. "
                "Anything else I can help with, or any other questions?"
            )
        else:  # email
            return (
                f"Got it, {caller_name} — when your email arrives at "
                "support at basic capital dot com, our team will be "
                "ready to help. Anything else I can help with, or any "
                "other questions?"
            )

    return record_followup


async def send_queue_entry_ping(
    webhook_url: str,
    *,
    caller_number: str,
    intent_summary: str,
    position: int,
) -> None:
    """Notify the team that a caller has entered the hold queue.

    Informational only — no "Take call in browser" button here. That
    button fires from escalation.py's `_send_slack_ping` once the
    caller has been dispatched out of the queue into the probe path
    (which is when there's actually a slot for the rep to fill).

    Mirrors `_send_slack_ping`'s text shape so the team's Slack channel
    has a consistent format across queue-entry and dispatch events.
    """
    payload = {
        "text": (
            f":hourglass_flowing_sand: *BC call queued (#{position} in line)*\n"
            f"*Caller:* {caller_number}\n"
            f"*Wants:* {intent_summary}\n"
            f"_Waiting for a rep to free up. Dispatch ping with pickup "
            f"button will follow when their turn comes._"
        )
    }
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(webhook_url, json=payload)
    resp.raise_for_status()
    logger.info(
        "queue-entry Slack ping sent (status=%s, position=%d)",
        resp.status_code, position,
    )


async def send_inbound_call_ping(
    webhook_url: str,
    *,
    caller_number: str,
    call_id: str,
    after_hours: bool,
) -> None:
    """Notify the team that a new inbound call has connected to the bot.

    Fires from main.get_agent on every inbound call — including FAQ-only
    calls that never escalate. Lightweight informational ping so the
    team has live awareness of call volume + can jump into the Cartesia
    dashboard for any call that catches their eye.

    No buttons, no Linear ticket here. Escalation pings (with the "Open
    Rep Dashboard" button + escalation_pending Linear ticket) fire
    separately from escalation.py if the caller asks for a human.

    Failures are non-fatal — caller is dialed back so a Slack outage
    must not block the call from connecting.
    """
    agent_id = os.environ.get("CARTESIA_AGENT_ID", "").strip()
    deeplink = ""
    if agent_id and call_id:
        # Cartesia's dashboard expects `ac_`-prefixed call IDs even
        # though CallRequest.call_id arrives without the prefix.
        call_id_for_url = call_id if call_id.startswith("ac_") else f"ac_{call_id}"
        deeplink = (
            f"\n<https://play.cartesia.ai/agents/{agent_id}"
            f"?tab=calls&call={call_id_for_url}|View live in Cartesia dashboard>"
        )

    icon = ":moon:" if after_hours else ":telephone_receiver:"
    mode_label = "after-hours" if after_hours else "business hours"
    payload = {
        "text": (
            f"{icon} *BC inbound call* ({mode_label})\n"
            f"*From:* {caller_number or '(unknown)'}"
            f"{deeplink}"
        )
    }
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(webhook_url, json=payload)
    resp.raise_for_status()
    logger.info(
        "inbound-call Slack ping sent (status=%s, after_hours=%s)",
        resp.status_code, after_hours,
    )


def _email_fallback_instruction(caller_name: str = "") -> str:
    """When Slack fails, return the exact wording for the bot to speak.
    No meta prefix — the system prompt tells the LLM to speak the return
    value verbatim."""
    name_part = f", {caller_name}" if caller_name else ""
    return (
        f"Sorry{name_part} — I'm having trouble logging that on our end "
        "right now. Please email support at basic capital dot com and "
        "our team will follow up there."
    )
