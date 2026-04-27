"""Post a callback request to Slack.

Used when escalate_to_human returns 'unavailable' and the caller has given
a callback number. Posts a structured message to a Slack incoming webhook
(reuses SLACK_WEBHOOK_URL — same channel as the incoming-call pings, just
different message format).

Exposed as a factory (`make_slack_ticket_tool(call_request)`) so the tool
can embed the Cartesia call_id and inbound caller number — Line's tool ctx
is empty.
"""

import logging
import os
from typing import Annotated

import httpx
from line.llm_agent.tools.decorators import loopback_tool
from line.voice_agent_app import CallRequest

logger = logging.getLogger(__name__)


def make_slack_ticket_tool(call_request: CallRequest):
    """Build the create_callback_ticket tool bound to this call's CallRequest."""

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    @loopback_tool
    async def create_callback_ticket(
        ctx,
        intent_summary: Annotated[
            str,
            "One sentence describing what the caller wants, in their own "
            "words. Becomes the message title in Slack.",
        ],
        callback_number: Annotated[
            str,
            "Callback number in any format the caller gave it (e.g. "
            "'415-555-1234' or '+14155551234').",
        ],
    ) -> str:
        """Post a callback request to Slack so the team can follow up.

        Call this BEFORE confirming to the caller, so a mid-sentence hangup
        doesn't lose the request.

        Returns a short instruction string for the LLM with the exact wording
        to use when confirming to the caller — or an email fallback if Slack
        is unreachable.
        """
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        if not webhook_url:
            logger.error("SLACK_WEBHOOK_URL not set — falling back to email")
            return _email_fallback_instruction()

        message = {
            "text": f":telephone_receiver: Callback requested: {intent_summary}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": ":telephone_receiver: Callback request",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Callback number:*\n{callback_number}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Inbound from:*\n{caller_number}",
                        },
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
            logger.warning("Slack ticket post failed: %s", e)
            return _email_fallback_instruction()

        logger.info("Slack callback ticket posted for call_id=%s", call_id)
        return (
            "Callback request logged successfully. Tell the caller: "
            f"'Got it — someone from our team will follow up at {callback_number} "
            "within one business day. Anything else I can help with?'"
        )

    return create_callback_ticket


def _email_fallback_instruction() -> str:
    """Instruction for the LLM when Slack is unreachable. Don't lie to the
    caller — redirect them to email."""
    return (
        "Callback request FAILED to log. Tell the caller: "
        "'I'm having trouble logging that right now — please email "
        "support at basic capital dot com and our team will follow up.'"
    )
