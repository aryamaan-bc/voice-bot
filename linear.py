"""Create a Linear ticket for a callback.

Called by the LLM after escalate_to_human returns "unavailable". Posts
directly to Linear's GraphQL API — no middleware.

Exposed as a factory (`make_linear_tool(call_request)`) so the tool can
embed the Cartesia call_id in the ticket for supervisor lookup — Line's
tool ctx is empty and doesn't carry call metadata.
"""

import logging
import os
from typing import Annotated

import httpx
from line.llm_agent.tools.decorators import loopback_tool
from line.voice_agent_app import CallRequest

logger = logging.getLogger(__name__)

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

CREATE_ISSUE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { identifier url }
  }
}
"""


def make_linear_tool(call_request: CallRequest):
    """Build the create_callback_ticket tool bound to this call's CallRequest."""

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    @loopback_tool
    async def create_callback_ticket(
        ctx,
        intent_summary: Annotated[
            str,
            "One sentence describing what the caller wants, in their own "
            "words. Becomes the Linear ticket title.",
        ],
        callback_number: Annotated[
            str,
            "Callback number in any format the caller gave it (e.g. "
            "'415-555-1234' or '+14155551234').",
        ],
    ) -> str:
        """Create a Linear ticket for a callback. Call this BEFORE confirming
        to the caller, so a mid-sentence hangup doesn't lose the request.

        Returns a short instruction string for the LLM containing either the
        ticket ID to read back, or an email-fallback line if ticket creation
        failed.
        """
        api_key = os.environ.get("LINEAR_API_KEY")
        team_id = os.environ.get("LINEAR_TEAM_ID")
        if not api_key or not team_id:
            logger.error("Linear env vars missing (LINEAR_API_KEY / LINEAR_TEAM_ID)")
            return _email_fallback_instruction()

        description = (
            f"**Callback number:** {callback_number}\n\n"
            f"**Caller wants:** {intent_summary}\n\n"
            f"**Inbound number (ANI):** {caller_number}\n\n"
            f"**Cartesia call ID:** `{call_id}` "
            f"(audio + transcript in Cartesia dashboard)\n"
        )

        variables = {
            "input": {
                "teamId": team_id,
                "title": intent_summary[:250],  # Linear title soft cap
                "description": description,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    LINEAR_GRAPHQL_URL,
                    json={"query": CREATE_ISSUE_MUTATION, "variables": variables},
                    headers={
                        "Authorization": api_key,  # Linear: raw key, no Bearer prefix
                        "Content-Type": "application/json",
                    },
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("Linear HTTP error: %s", e)
            return _email_fallback_instruction()
        except ValueError as e:
            logger.warning("Linear non-JSON response: %s", e)
            return _email_fallback_instruction()

        if data.get("errors"):
            logger.warning("Linear GraphQL errors: %s", data["errors"])
            return _email_fallback_instruction()

        result = (data.get("data") or {}).get("issueCreate") or {}
        if not result.get("success") or not result.get("issue"):
            logger.warning("Linear returned unexpected payload: %s", data)
            return _email_fallback_instruction()

        ticket_id = result["issue"]["identifier"]
        logger.info("Linear ticket %s created for call_id=%s", ticket_id, call_id)
        return (
            f"Ticket {ticket_id} created. Tell the caller: "
            f"'Ticket {ticket_id} is all set — someone from our team will "
            f"follow up at {callback_number} within one business day.'"
        )

    return create_callback_ticket


def _email_fallback_instruction() -> str:
    """Instruction for the LLM when Linear is unreachable. Don't lie to the
    caller — redirect them to email."""
    return (
        "Ticket creation FAILED. Tell the caller: "
        "'I'm having trouble logging that right now — please email "
        "support at basic capital dot com and our team will follow up.'"
    )
