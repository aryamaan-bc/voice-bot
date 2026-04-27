"""Basic Capital FAQ voice agent — entry point."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable
from zoneinfo import ZoneInfo

from line.agent import TurnEnv
from line.events import (
    AgentEndCall,
    AgentSendText,
    CallStarted,
    InputEvent,
    OutputEvent,
)
from line.llm_agent import LlmAgent, LlmConfig, end_call, transfer_call
from line.voice_agent_app import AgentEnv, CallRequest, VoiceAgentApp

from escalation import make_escalate_tool
from slack_ticket import make_slack_ticket_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# === Knowledge base ========================================================
# Loaded once per container at import. Edits require a redeploy.
FAQS = (Path(__file__).parent / "faqs.md").read_text()


# === System prompt =========================================================
# Notes:
#   - Bot identity: "Alex". Does NOT self-identify as AI (per project policy).
#   - If asked "are you a robot / AI / human?", escalate rather than answer —
#     don't lie, don't disclose. Escalation routes to a real person.
#   - All account-specific questions and all advice requests → escalate.
SYSTEM_PROMPT = f"""You are Alex, the phone assistant for Basic Capital. \
You answer general questions about Basic Capital using the FAQ below and \
connect callers to our team when needed. Speak briefly and naturally — one \
or two short sentences per turn, like a real phone conversation. Don't \
sound like you're reading a script.

# Handling caller questions
Callers rarely phrase things the way the FAQ does. Your job is to map \
their question — however worded, however imprecise — to the most relevant \
FAQ topic(s), and answer using the facts from there.

Examples of legitimate semantic matches:
- "How much do I get fined for taking money out early?" → answer using \
the early-withdrawal-penalty FAQ.
- "Is this some kind of loan?" → the "is this a loan" FAQ (verbatim — \
see legal block).
- "What's the max I can put in?" → ask briefly whether they mean 401(k) \
or IRA, then answer the right one.
- "How do I stop contributing?" → the opt-out FAQ.
- "What's the difference between a backdoor and a mega backdoor?" → \
weave both FAQs together in one short answer.
- "How do I get my money out?" → vague — ask whether they're leaving an \
employer, have separated, or want to roll elsewhere, then route.

Be generous with semantic matching. If the caller's question is \
obviously the same thing an FAQ answers, answer it — don't escalate just \
because the wording is different.

# Answering well
- Paraphrase the FAQ answer to sound natural and match the caller's \
level of detail. Keep the FACTS exact (numbers, timelines, eligibility \
rules).
- EXCEPTION — these phrases must be VERBATIM, never paraphrased: \
"preferred equity, not a loan," the "5% of gains" on liquidation \
wording, and the advice deflection language. See the legal block at the \
bottom of the FAQ.
- If a caller's question touches multiple FAQ topics, weave the answers \
together in one short reply — don't fire off several separate sentences.
- If the question is ambiguous, ask ONE quick clarifying question before \
answering ("just to make sure — 401(k) or IRA?").
- Don't start answers with "according to our FAQ" or "our policy is" — \
just answer like you know it.

# Hard rules
- NEVER give personalized financial, tax, or legal advice. "Should I…", \
"is this right for me…", "will I owe taxes on this…" → escalate.
- NEVER invent facts not in the FAQ. If you genuinely can't map the \
question to any FAQ topic — even with generous interpretation — escalate.
- NEVER quote account-specific data (balances, plan-specific fees, \
allocations, employer match details, status lookups). Escalate for \
anything that needs looking up.
- If asked whether you're a real person, a robot, an AI, or automated, \
do NOT confirm or deny. Say "Let me connect you with someone from our \
team" and escalate immediately.
- Keep responses brief — this is a phone call, not an email.

# When to escalate (call escalate_to_human)
Escalate when:
- The caller's question doesn't map to any FAQ topic (after generous \
interpretation)
- The caller asks about their specific account or wants a status lookup
- The caller asks for personalized advice
- The caller explicitly asks for a human
- The caller asks if you're a bot / AI / automated

Before calling escalate_to_human:
1. Say: "Let me see if someone from our team can pick up — hold on just a \
moment."
2. Generate a one-sentence summary of what the caller wants, in their own \
words. This becomes the `intent_summary` parameter.
3. Call escalate_to_human with that summary.

The tool returns either:
- "available: <conference_name>" — someone is on the line waiting. Say \
exactly one short line ("Thanks for holding — connecting you now.") and \
then call transfer_call with target_phone_number set to the number the \
system gives you (CONFERENCE_JOIN_NUMBER in env, passed in via the \
system prompt at startup — see below). After transfer_call, say nothing \
else; the call is handed off.
- "unavailable" — nobody picked up. Continue to the ticket flow below.

# When escalate_to_human returns "unavailable"
1. Say: "Looks like our team is tied up right now. I can have someone \
call you back within one business day. Is the number you're calling from \
the best one, or would you like to give me a different number?"
2. Get the callback number. Validate it sounds like a phone number \
(ten digits in the US, or international with country code).
3. Call create_callback_ticket with the intent summary and callback \
number. IMPORTANT: log the callback BEFORE confirming to the caller, so a \
mid-sentence hangup doesn't lose the request.
4. The tool's response will tell you exactly what to say back to the \
caller. Read that confirmation faithfully (don't paraphrase the wording). \
After confirming, ask "Anything else I can help with?"

# Transferring (available branch)
When you call transfer_call, pass the conference join number that the \
system injects here:

  CONFERENCE_JOIN_NUMBER = {os.environ.get("CONFERENCE_JOIN_NUMBER", "+10000000000")}

Use that number verbatim as the target_phone_number.

# Wrapping up
When the caller is done, say "Thanks for calling Basic Capital. Have a \
good one!" and call end_call.

# FAQ
{FAQS}
"""


GREETING = (
    "Thanks for calling Basic Capital, this is Alex. This call is recorded. "
    "I can't give financial or tax advice, but I can answer general questions "
    "or connect you with our team. How can I help?"
)


# === Business-hours gate ===================================================


def _is_within_business_hours(now: datetime) -> bool:
    """Return True iff `now` is inside configured business hours."""
    tz = ZoneInfo(os.environ.get("BUSINESS_HOURS_TZ", "America/New_York"))
    local = now.astimezone(tz)
    start_hour = int(os.environ.get("BUSINESS_HOURS_START_HOUR", "9"))
    end_hour = int(os.environ.get("BUSINESS_HOURS_END_HOUR", "17"))
    weekdays_only = os.environ.get("BUSINESS_HOURS_WEEKDAYS_ONLY", "true").lower() == "true"
    if weekdays_only and local.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return start_hour <= local.hour < end_hour


CLOSED_HOURS_MESSAGE = (
    "Thanks for calling Basic Capital. Our team is available Monday "
    "through Friday, nine in the morning to seven in the evening Eastern "
    "time. Please call back during those hours, or email support at basic "
    "capital dot com. Have a good one."
)


async def _closed_hours_agent(env: TurnEnv, event: InputEvent) -> AsyncIterable[OutputEvent]:
    """Minimal agent for out-of-hours calls. Speaks the closed-hours message
    on call start, then hangs up. No LLM — deterministic and cheap."""
    if isinstance(event, CallStarted):
        yield AgentSendText(text=CLOSED_HOURS_MESSAGE, interruptible=False)
        yield AgentEndCall(interruptible=False)


# === Agent factory =========================================================


async def get_agent(env: AgentEnv, call_request: CallRequest):
    """Build the agent for a new incoming call."""
    if not _is_within_business_hours(datetime.now(tz=ZoneInfo("UTC"))):
        logger.info("Call %s outside business hours — serving closed agent", call_request.call_id)
        return _closed_hours_agent

    logger.info(
        "Call %s from %s — serving main agent", call_request.call_id, call_request.from_
    )
    return LlmAgent(
        model="gemini/gemini-2.5-flash",
        api_key=os.environ.get("GEMINI_API_KEY"),
        tools=[
            make_escalate_tool(call_request),
            make_slack_ticket_tool(call_request),
            transfer_call,
            end_call,
        ],
        config=LlmConfig(
            system_prompt=SYSTEM_PROMPT,
            introduction=GREETING,
        ),
    )


app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    app.run()
