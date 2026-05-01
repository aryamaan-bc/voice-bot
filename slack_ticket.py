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

logger = logging.getLogger(__name__)


def make_followup_tool(call_request: CallRequest):
    """Build the record_followup tool bound to this call's CallRequest."""

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
