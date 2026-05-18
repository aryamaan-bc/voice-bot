"""Per-call logging to Linear (and a mirrored Slack post-call summary).

`log_call_complete(...)` is the unified entry point. Called from every
call-termination path (end_call_with_goodbye, escalate_to_human's transfer
branch, the closed-hours agent) so the ops team gets a Linear ticket per
call with caller info, recap, outcome, and a link to Cartesia's audio +
transcript.

Best-effort: failures are logged but don't propagate. A bad Linear day
shouldn't block a clean call wrap-up.
"""

import logging
import os
from typing import Literal, Optional

import httpx

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

Outcome = Literal[
    "answered_from_faq",
    "callback_logged",
    "email_logged",
    "transferred",
    "closed_hours",
    "voicemail",
    "abandoned",
    "escalation_pending",
    "queue_waiting",
    "abandoned_in_queue",
    "voicemail_logged",
    "other",
]

_OUTCOME_LABELS = {
    "answered_from_faq": "Answered from FAQ",
    "callback_logged": "Callback requested",
    "email_logged": "Email follow-up expected",
    "transferred": "Transferred to human",
    "closed_hours": "Hit closed-hours message",
    "voicemail": "Voicemail captured (after hours)",
    "abandoned": "Caller hung up mid-call",
    "escalation_pending": "Escalation started — outcome pending",
    "queue_waiting": "Queued — waiting for a rep",
    "abandoned_in_queue": "Caller hung up while queued",
    "voicemail_logged": "Voicemail + callback captured (v2 queue press-1)",
    "other": "Other",
}


async def log_escalation_started(
    *,
    call_id: str,
    caller_number: str,
    intent_summary: str,
) -> None:
    """Fire a Linear ticket + Slack DM the moment escalate_to_human is
    invoked, BEFORE the probe-wait runs.

    Purpose: guarantee a paper trail even when downstream steps fail
    silently (e.g. Cartesia's LLM hijacks the tool mid-wait and the call
    drifts without a clean termination path). Worst case the team gets a
    duplicate ticket per escalation (start + outcome) — strictly better
    than zero tickets when something goes wrong.
    """
    recap = (
        f"Caller asked to be escalated to a human. The bot has started "
        f"the handoff flow but the outcome is pending — see follow-up "
        f"ticket(s) for the final result."
    )
    await log_call_complete(
        call_id=call_id,
        caller_number=caller_number,
        caller_name=None,
        intent_summary=intent_summary,
        outcome="escalation_pending",
        recap=recap,
    )


async def log_call_complete(
    *,
    call_id: str,
    caller_number: str,
    caller_name: Optional[str],
    intent_summary: str,
    outcome: Outcome,
    recap: str,
) -> None:
    """Fire the post-call notifications for the ops team:
      1. Create a Linear ticket
      2. Send a Slack DM with the ticket link

    Both are best-effort. A failure of one doesn't block the other.
    """
    name_label = caller_name.strip() if caller_name and caller_name.strip() else "didn't give a name"
    outcome_label = _OUTCOME_LABELS.get(outcome, outcome)

    ticket_id, ticket_url = await _create_linear_ticket(
        call_id=call_id,
        caller_number=caller_number,
        name_label=name_label,
        intent_summary=intent_summary,
        outcome_label=outcome_label,
        recap=recap,
    )

    await _send_slack_summary(
        call_id=call_id,
        caller_number=caller_number,
        name_label=name_label,
        intent_summary=intent_summary,
        outcome_label=outcome_label,
        recap=recap,
        ticket_id=ticket_id,
        ticket_url=ticket_url,
    )


async def _create_linear_ticket(
    *,
    call_id: str,
    caller_number: str,
    name_label: str,
    intent_summary: str,
    outcome_label: str,
    recap: str,
) -> tuple[Optional[str], Optional[str]]:
    """POST a new Linear ticket. Returns (identifier, url) on success, or
    (None, None) if Linear is unconfigured/unreachable."""
    api_key = os.environ.get("LINEAR_API_KEY", "")
    team_id = os.environ.get("LINEAR_TEAM_ID", "")
    if not api_key or not team_id:
        logger.warning(
            "Linear not configured — skipping ticket for call_id=%s", call_id
        )
        return None, None

    # Cartesia's deep-link format uses query params (not path segments):
    #   /agents/{agent_id}?tab=calls&call={ac_sid_xxx}
    # The call_id we receive from CallRequest doesn't have the "ac_" prefix;
    # the dashboard URL needs it.
    dashboard_call_id = call_id if call_id.startswith("ac_") else f"ac_{call_id}"
    agent_id = os.environ.get("CARTESIA_AGENT_ID", "")
    if agent_id:
        deep_link = (
            f"https://play.cartesia.ai/agents/{agent_id}"
            f"?tab=calls&call={dashboard_call_id}"
        )
        audio_line = f"**Audio + transcript:** {deep_link}"
    else:
        audio_line = (
            "**Audio + transcript:** find this call in Cartesia's dashboard "
            "(set CARTESIA_AGENT_ID env var to get a deep-link URL)"
        )

    description = (
        f"**Outcome:** {outcome_label}\n\n"
        f"**Caller name:** {name_label}\n"
        f"**Inbound from:** {caller_number}\n\n"
        f"**What they wanted:**\n{intent_summary}\n\n"
        f"**Recap:**\n{recap}\n\n"
        f"---\n\n"
        f"{audio_line}\n"
        f"**Cartesia call ID:** `{dashboard_call_id}`\n"
    )

    title_caller = name_label if name_label != "didn't give a name" else "caller"
    title = f"Call from {title_caller} — {intent_summary[:140]}"

    variables = {
        "input": {
            "teamId": team_id,
            "title": title[:250],
            "description": description,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                LINEAR_GRAPHQL_URL,
                json={"query": CREATE_ISSUE_MUTATION, "variables": variables},
                headers={
                    "Authorization": api_key,  # raw key, no Bearer
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Linear ticket POST failed: %s", e)
        return None, None

    if data.get("errors"):
        logger.warning("Linear GraphQL errors: %s", data["errors"])
        return None, None

    result = (data.get("data") or {}).get("issueCreate") or {}
    if not result.get("success") or not result.get("issue"):
        logger.warning("Linear unexpected payload: %s", data)
        return None, None

    issue = result["issue"]
    ident = issue.get("identifier")
    url = issue.get("url")
    logger.info("Linear ticket %s created for call_id=%s", ident, call_id)
    return ident, url


async def _send_slack_summary(
    *,
    call_id: str,
    caller_number: str,
    name_label: str,
    intent_summary: str,
    outcome_label: str,
    recap: str,
    ticket_id: Optional[str],
    ticket_url: Optional[str],
) -> None:
    """Send a post-call summary DM to Slack, with the Linear ticket link
    if creation succeeded."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping post-call DM")
        return

    if ticket_id and ticket_url:
        ticket_line = f"*Linear ticket:* <{ticket_url}|{ticket_id}>"
    else:
        ticket_line = "*Linear ticket:* (not created — Linear unconfigured/down)"

    dashboard_call_id = call_id if call_id.startswith("ac_") else f"ac_{call_id}"
    agent_id = os.environ.get("CARTESIA_AGENT_ID", "")
    if agent_id:
        deep_link = (
            f"https://play.cartesia.ai/agents/{agent_id}"
            f"?tab=calls&call={dashboard_call_id}"
        )
        audio_link = f"<{deep_link}|audio + transcript>"
    else:
        audio_link = f"call ID `{dashboard_call_id}`"

    body = {
        "text": f":memo: Call complete: {name_label} — {intent_summary}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":memo: Call complete"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Caller:*\n{name_label}"},
                    {"type": "mrkdwn", "text": f"*From:*\n{caller_number}"},
                    {"type": "mrkdwn", "text": f"*Outcome:*\n{outcome_label}"},
                    {"type": "mrkdwn", "text": ticket_line},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Wanted:* {intent_summary}\n\n*Recap:* {recap}",
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": audio_link}],
            },
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(webhook_url, json=body)
        resp.raise_for_status()
        logger.info("Slack post-call summary sent for call_id=%s", call_id)
    except Exception as e:
        logger.warning("Slack post-call summary failed: %s", e)
