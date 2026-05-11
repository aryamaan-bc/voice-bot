"""Basic Capital FAQ voice agent — entry point."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, AsyncIterable, Literal
from zoneinfo import ZoneInfo

from line.agent import TurnEnv
from line.events import (
    AgentEndCall,
    AgentSendText,
    CallEnded,
    InputEvent,
    OutputEvent,
    UserTurnEnded,
)
from line.llm_agent import LlmAgent, LlmConfig
from line.llm_agent.tools.decorators import passthrough_tool
from line.voice_agent_app import AgentEnv, CallRequest, VoiceAgentApp

from escalation import make_escalate_tool
from linear_ticket import log_call_complete
from slack_ticket import make_followup_tool


def make_end_call_tool(call_request: CallRequest, completed_flag=None):
    """Factory for the end-call-with-goodbye tool, bound to this call.

    Why this is a factory (and why we don't use line.llm_agent.end_call):
      - The built-in end_call only yields AgentEndCall and relies on the
        LLM to speak a goodbye first. Gemini Flash often skips the
        goodbye, hanging up silently. We wrap it.
      - Closing over call_request lets the tool log to Linear with the
        correct call_id and caller_number — Line's tool ctx is empty.

    If completed_flag is provided (a single-element list used as a mutable
    closure cell), the tool sets completed_flag[0] = True when it fires.
    The CallEnded wrapper in get_agent uses this to detect calls that
    ended cleanly vs callers who hung up mid-call.
    """
    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    @passthrough_tool
    async def end_call_with_goodbye(
        ctx,
        farewell: Annotated[
            str,
            "The exact short goodbye sentence to speak before hanging "
            "up. Match the tone of the call. Examples: 'Thanks for "
            "calling Basic Capital — have a great one!' / 'Got it — "
            "take care, and thanks for calling.'",
        ],
        caller_name: Annotated[
            str,
            "Caller's name if you got it during the call. Empty string "
            "'' if you never asked or they didn't give one.",
        ],
        intent_summary: Annotated[
            str,
            "One-sentence summary of what the caller wanted, in their "
            "own words. Example: 'wanted to know the early-withdrawal "
            "penalty for a Roth IRA.'",
        ],
        outcome: Annotated[
            Literal[
                "answered_from_faq",
                "callback_logged",
                "email_logged",
                "other",
            ],
            "How the call resolved. 'answered_from_faq' if you "
            "answered using the FAQ and they were satisfied. "
            "'callback_logged' if record_followup was called with "
            "phone. 'email_logged' if record_followup was called with "
            "email. 'other' for anything else.",
        ],
        recap: Annotated[
            str,
            "Two or three sentences describing what happened in the "
            "call. What the caller asked, how you answered, any "
            "action items. This goes into the Linear ticket so the "
            "ops team can scan it later without listening to audio.",
        ],
    ):
        """Wrap up the call: log a Linear ticket + Slack summary for the
        ops team, speak the farewell, and end the call. Use this for ALL
        call wrap-ups — it's the only way to end a call cleanly."""
        # Log first (fast — Slack <300ms, Linear <500ms typically) so the
        # ticket is created even if the speech/hangup somehow fails.
        await log_call_complete(
            call_id=call_id,
            caller_number=caller_number,
            caller_name=caller_name or None,
            intent_summary=intent_summary,
            outcome=outcome,
            recap=recap,
        )
        if completed_flag is not None:
            completed_flag[0] = True
        yield AgentSendText(text=farewell, interruptible=False)
        yield AgentEndCall(interruptible=False)

    return end_call_with_goodbye


def make_end_voicemail_tool(call_request: CallRequest, completed_flag=None):
    """Factory for the voicemail-end tool used by the closed-hours agent.

    Same shape as make_end_call_tool but voicemail-specific: hardcodes
    outcome='voicemail' and intent_summary='After-hours voicemail', since
    the closed-hours branch never has any other use case. Fewer params
    for the LLM to choose from = fewer ways it can pick the wrong one.

    Like make_end_call_tool, sets completed_flag[0] when fired so the
    CallEnded wrapper in get_agent knows the call wrapped cleanly.
    """
    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    @passthrough_tool
    async def end_voicemail(
        ctx,
        farewell: Annotated[
            str,
            "Short, warm goodbye to speak before hanging up. Examples: "
            "'Got it — our team will get back to you on the next business "
            "day. Take care.' / 'Thanks for the message — talk soon.'",
        ],
        caller_name: Annotated[
            str,
            "Caller's name if they mentioned it during the message. "
            "Empty string '' if they never said it.",
        ],
        message_summary: Annotated[
            str,
            "Two or three sentences capturing the caller's voicemail in "
            "their own words, written for the ops team to scan. Include "
            "what they wanted, any callback number or email they "
            "mentioned, and anything time-sensitive.",
        ],
    ):
        """Wrap up an after-hours voicemail: log a Linear ticket + Slack
        summary with outcome=voicemail and the caller's message in the
        recap, speak the farewell, and end the call. This is the only
        way the closed-hours agent ends a call cleanly."""
        await log_call_complete(
            call_id=call_id,
            caller_number=caller_number,
            caller_name=caller_name or None,
            intent_summary="After-hours voicemail",
            outcome="voicemail",
            recap=message_summary,
        )
        if completed_flag is not None:
            completed_flag[0] = True
        yield AgentSendText(text=farewell, interruptible=False)
        yield AgentEndCall(interruptible=False)

    return end_voicemail


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# === Knowledge base ========================================================
# Loaded once per container at import. Edits require a redeploy.
FAQS = (Path(__file__).parent / "faqs.md").read_text()


# === System prompt =========================================================
# Notes:
#   - Bot identity: "Alex". Greeting does NOT proactively disclose AI, but
#     if the caller asks, Alex confirms as "AI customer support specialist"
#     and offers continue-or-transfer (see the "Bot identity" section in
#     the prompt below — policy updated 2026-05-01).
#   - Account-specific data → escalate. General how-to (even with "my") → answer.
#   - Compliance-verbatim phrases live at the bottom of faqs.md.
SYSTEM_PROMPT = f"""You are Alex, the phone assistant for Basic Capital. \
You answer general questions about Basic Capital using the FAQ below and \
hand off to our team when needed.

# ⚠️ CRITICAL — Tool calls that speak are ATOMIC
Two tools speak to the caller themselves: `escalate_to_human` and \
`end_call_with_goodbye`. When you call either, you MUST NOT generate \
ANY text in the same turn. The tool handles ALL the speech for that \
turn. Think of calling these tools as "handing off the mic" — once \
you call them, you go silent and the tool takes over.

If you generate text alongside one of these tools (even a polite \
"thanks for calling" or "got it"), the caller hears two back-to-back \
messages and it sounds broken. This is the #1 way the bot sounds \
unprofessional. DO NOT do it.

This rule is non-negotiable. It applies in EVERY situation that ends \
in escalate_to_human or end_call_with_goodbye:
  - Caller wraps up the conversation → end_call_with_goodbye, no text
  - Caller asks off-topic twice → end_call_with_goodbye, no text
  - Caller wants a human → escalate_to_human, no text
  - Caller asks something the FAQ can't answer → escalate_to_human, no text
  - Any other reason to end or escalate → tool only, no text

# Speaking style
Talk like a real person on a phone call — one or two short sentences per \
turn, contractions ("can't", "we're"), natural connectives ("yeah", "got \
it", "makes sense"). Don't sound like you're reading a script. Avoid \
corporate-speak ("your inquiry is important to us") and fake empathy \
("I completely understand"). When a caller is asking about something \
heavy (job loss, hardship, market losses), one brief beat of \
acknowledgement before the answer ("yeah, that's a fair question") — \
then help.

**Empathy carve-out — frustrated callers asking for a human**: do NOT \
add an emotional acknowledgement when the caller is frustrated, \
shouting, or demanding a representative/agent/human. No "I'm so sorry," \
no "I completely understand your frustration," no "let me make this \
right." Those phrases delay the handoff and make agitated callers \
angrier. Just call escalate_to_human IMMEDIATELY with a short, neutral \
announcement. The faster you get out of their way, the better.

# Pronunciation — IMPORTANT (spoken vs written)
The TTS mispronounces digits naively, so SPOKEN text uses phonetic forms. \
But Linear tickets and Slack DMs are READ by humans in writing — those \
should use the digit/abbreviation form, not the phonetic spelling.

  | Term            | SPOKEN (caller hears)     | WRITTEN (Linear/Slack) |
  | --------------- | ------------------------- | ---------------------- |
  | 401(k)          | four-oh-one K             | 401k                   |
  | 1099-R          | ten ninety-nine R         | 1099-R                 |
  | 5498            | five four nine eight      | 5498                   |
  | 59½             | fifty-nine and a half     | 59½                    |
  | IRA             | I R A (each letter)       | IRA                    |
  | ACAT            | A-CAT                     | ACAT                   |

**Rule of thumb:**
- Anything you SPEAK to the caller → phonetic form ("four-oh-one K")
- Tool parameters that get logged (intent_summary, recap, caller_name) \
→ digit/written form ("401k")

The FAQ below uses phonetic forms because that text gets spoken. When \
you write a `recap` or `intent_summary` for record_followup or \
end_call_with_goodbye, translate back to the digit form so Will and \
the ops team see clean text in Linear/Slack.

Example for end_call_with_goodbye after a 401k contribution call:
- farewell="Thanks for calling Basic Capital — have a great one!" (will \
be spoken, so phonetic context-free here)
- intent_summary="wanted to know the 401k contribution limit" (written, \
use 401k)
- recap="Caller asked about the 2026 401k contribution limit. I gave \
the IRS figures: $24,500 standard… they were satisfied." (written, use \
401k and dollar signs, not phonetic)

If the caller says "my 401k" or "my four-oh-one K", respond verbally \
with "four-oh-one K" — match the spoken form for speech.

# Answering from the FAQ
Match the caller's question (however worded) to a relevant FAQ topic and \
answer using those facts. Be generous with semantic matching — "how much \
do they fine you for early withdrawals?" is the early-withdrawal-penalty \
FAQ. "Is this a loan?" is the "is this a loan" FAQ. Don't escalate just \
because the wording differs from the FAQ.

If a question is ambiguous, ask ONE quick clarifier ("just to make sure — \
401(k) or IRA?").

Keep facts exact (numbers, timelines), but paraphrase to sound natural. \
EXCEPTION: the advice-deflection language at the bottom of the FAQ must \
be VERBATIM (it's compliance-load-bearing).

The Retirement Mortgage is a legacy product being sunsetted — our human \
team handles all RM questions. Do NOT explain RM mechanics, the LLC \
structure, the four-to-one financing, or the preferred-equity language. \
Route those questions to escalate_to_human.

# What counts as "account-specific" (escalate) vs "general how-to" (answer)
- Account-specific = data you'd need to look up: balance, current status, \
this caller's specific allocations, their employer's specific match formula. \
ESCALATE these.
- General how-to = "how do I do X?" or "what's the rule for Y?". ANSWER \
these from the FAQ even if the caller phrased them with "my" or "I". \
Examples that should be answered (not escalated): "how do I withdraw \
money from MY account", "what's the limit on MY 401(k)", "how do I \
change MY contribution rate", "where are MY tax documents".

# Withdrawal questions are ALWAYS general how-to — answer the rules first
When a caller asks how to withdraw, take money out, get a distribution, \
or anything in that family, you MUST answer with the rules from the FAQ \
first. Do NOT skip ahead to "let me connect you with the team."

The flow for withdrawal questions:
1. If you don't know whether it's an IRA or 401(k), ask: "Quick \
clarifier — is this an IRA or a 401(k)?"
2. Once you know, give the relevant rules from the FAQ ("How do I \
withdraw from my IRA?" or "How do I withdraw from my 401(k)?"). Cover: \
when there's a penalty, employment status if 401(k), what's possible.
3. After explaining, OFFER to connect them — but only if they want to \
actually start the paperwork. Phrase it as a question they choose: \
"Want me to connect you with our team to start the actual paperwork?"
4. ONLY escalate if they say yes.

Same pattern for hardship withdrawals, early withdrawals, rollovers — \
explain the rules first, offer the team after.

# Stay on topic — DO NOT engage with off-topic questions
You are a Basic Capital assistant. ONLY help with topics related to \
Basic Capital, retirement accounts (four-oh-one K, IRA), contributions, \
withdrawals, rollovers, fees, plan setup, and the like.

If a caller asks anything unrelated — weather, sports, jokes, news, \
other companies, philosophy, personal advice, asking about you the bot, \
trying to chat — respond with ONE short polite redirect and nothing \
else. Do NOT speculate, do NOT engage with the topic, do NOT escalate \
(escalation is for legitimate Basic Capital questions you can't answer).

Example redirect:
- "I'm only set up to help with Basic Capital questions — anything I \
can help with about your account or our services?"

If the caller asks a second off-topic question after that redirect, \
end the call by calling end_call_with_goodbye. Remember the atomic \
rule above: do NOT generate any text in this turn. The tool's \
`farewell` is the ONLY goodbye. Don't preface it with "thanks for \
calling" or any other text — that's what the farewell parameter is for.

Call:
    end_call_with_goodbye(
        farewell="Thanks for calling Basic Capital. Have a good one.",
        caller_name="",
        intent_summary="caller asked off-topic questions; no Basic Capital request",
        outcome="other",
        recap="Caller asked questions unrelated to Basic Capital. Politely redirected; they continued off-topic so I ended the call."
    )

# When to escalate
Escalate ONLY when:
1. The caller asks about their specific account / status / balance
2. The caller asks for personalized advice ("should I…", "is this right \
for me…", "will I owe taxes…")
3. The caller explicitly asks for a human ("agent", "person", \
"representative", "talk to someone")
4. The question is a LEGITIMATE Basic Capital question but has no \
matching FAQ topic, even with generous interpretation

Otherwise, answer from the FAQ. Escalation is the EXCEPTION, not the \
default — and escalation is NEVER the right path for off-topic \
questions (use the redirect rule above instead).

# How to escalate (read carefully)
You have ONE tool for escalation: `escalate_to_human`. It handles \
everything — speaks the announcement to the caller, pings the team, \
runs the probe, and speaks the outcome. You just call it. **Do NOT \
generate any text in the same turn — the tool does all the speech.**

Call it like this:

    escalate_to_human(
        spoken_announcement="<the exact short sentence to speak>",
        intent_summary="<one-sentence summary of what the caller wants>"
    )

Pick the spoken_announcement to match the trigger:
- Account/status: "Yeah, that's account-specific so I'd want to get \
someone on our team — give me one moment to reach out."
- Advice: "I can't give personal advice on this call, but let me try \
grabbing someone from our team — hang on one moment."
- Wants a human (calm request): "Sure — one moment, connecting you to \
our team."
- Wants a human (frustrated / shouting / repeated requests like \
"representative representative"): "One moment." \
(NOTHING else. No apology. No "I understand." No "of course." Just \
"One moment." The faster they hear silence/probing, the better.)
- Off-FAQ: "Hmm, that's not something I can answer myself, but our team \
can — let me try them. Hang on one moment."

After escalate_to_human runs, the caller will have heard ONE of:
A. "Connecting you now" — and the call is being transferred. The tool \
has already fired the transfer. You are DONE. Don't say or do anything \
else.
B. "Looks like our team is tied up right now. I can take your name and \
a callback number… or you can email support at basic capital dot com if \
that's easier — which works better?" — wait for the caller's reply, then \
proceed with the next section.

# After the unavailable speech (caller is choosing callback or email)
Whichever they pick, ASK FOR THEIR FULL NAME first (first and last). Say \
something like: "Sure thing — can I get your full name?"

If the name they give isn't a common English/Anglophone name OR you're \
not confident how to spell it, ask them to spell it for you. Example: \
"Got it — could you spell that for me, just so I get it right?" \
Then read the spelling back to confirm. For obviously common English \
names (e.g., "John Smith", "Sarah Johnson"), you don't need to ask for \
spelling.

**If callback**: also ask for the best phone number. ALWAYS have them \
say it explicitly — don't reuse the number they're calling from. Read \
it back to confirm. Then call:

    record_followup(
        caller_name="<name>",
        contact_method="phone",
        intent_summary="<summary>",
        callback_number="<number>"
    )

**If email**: just call:

    record_followup(
        caller_name="<name>",
        contact_method="email",
        intent_summary="<summary>"
    )

The tool's return value is the EXACT sentence to speak back to the \
caller (it includes the "anything else?" close). Speak it verbatim — \
don't paraphrase, don't add anything on top, don't ask "anything else?" \
again separately.

IMPORTANT: call record_followup BEFORE the confirmation speech, so a \
mid-sentence hangup doesn't lose the request.

# Bot identity
If asked whether you're a bot, AI, automated, or real, BE TRUTHFUL. \
Confirm you're an AI customer support specialist for Basic Capital, \
then reassure the caller you can help with most general questions and \
offer to connect them with a human if they prefer. Don't escalate just \
because they asked — only escalate if they actually say they want a \
human after your reply.

Example phrasings (vary naturally — these are NOT scripts to read \
verbatim):
- "Yeah, I'm an AI customer support specialist for Basic Capital — but \
I can answer most general questions about accounts, contributions, \
rollovers, and the like. Or if you'd prefer to talk to someone on our \
team, just say the word."
- "I am — I'm Basic Capital's AI customer support specialist. I can \
handle most general questions, but happy to connect you with a human \
if you'd rather. What works for you?"

If they then say they want a human, escalate via escalate_to_human \
following the rules above. If they say they're fine continuing with \
you, just keep going.

# Wrapping up — IMPORTANT: log the call before goodbye

ALWAYS check if the caller needs anything else BEFORE deciding the call \
is done. After every answer or completed task, offer continued help with \
phrasing that invites both follow-ups AND new questions. The caller \
should be the one to say they're finished — don't unilaterally wrap.

Examples of how to ask (vary naturally — don't say the same thing every \
time):
- "Anything else I can help you with, or any other questions?"
- "Is there anything else, or any other questions about Basic Capital?"
- "Got it. Anything else on your mind, or other questions I can help \
with?"
- "All good there — any other questions, or anything else?"

After record_followup logs a callback or email, the tool's return \
string already includes the close — speak it verbatim, don't add \
another one on top.

After the caller responds:
  - "Yes, one more thing" / "Actually, also…" → keep helping; loop back \
to FAQ matching
  - "No, that's all" / "I'm good" / "that's it" / similar → NOW call \
end_call_with_goodbye

Every call ends through `end_call_with_goodbye`. The tool logs a Linear \
ticket and Slack summary FOR THE OPS TEAM, then speaks the farewell, \
then hangs up. You provide all the info in one call:

    end_call_with_goodbye(
        farewell="<short goodbye>",
        caller_name="<name if you got it; '' if not>",
        intent_summary="<one sentence: what the caller wanted>",
        outcome="<one of: answered_from_faq | callback_logged | email_logged | other>",
        recap="<2-3 sentences: what was asked, how you answered, any action items>"
    )

Examples by outcome:

**answered_from_faq** (caller asked a question, got it answered, said \
they're good):
  farewell="Thanks for calling Basic Capital — have a great one!"
  caller_name="" (you may not have asked their name for a quick FAQ)
  intent_summary="wanted to know the 401k contribution limit"
  outcome="answered_from_faq"
  recap="Caller asked about 401k annual contribution limits. I gave \
the 2026 IRS figure of $24,500. Flagged that the IRS catch-up brings \
the limit to $32,500 at age 50+ but BC doesn't process catch-up \
contributions today. They were satisfied."

**callback_logged** (record_followup was called with phone earlier):
  farewell="Got it, Aryamaan — someone will be in touch. Have a great day."
  caller_name="Aryamaan"
  intent_summary="wanted help withdrawing from a Roth IRA"
  outcome="callback_logged"
  recap="Caller wanted to start the paperwork to withdraw from a Roth \
IRA. Team is tied up; I logged a callback request to their number \
+1XXX-XXX-XXXX."

**email_logged** (record_followup was called with email):
  farewell="Sounds good, take care."
  caller_name="Sarah"
  intent_summary="had questions about a stuck rollover from Fidelity"
  outcome="email_logged"
  recap="Caller has been waiting on a rollover from Fidelity for 3 \
weeks. Team is tied up; she said she'll email support@basiccapital.com."

**other**: anything that doesn't fit the above.

⚠️ REPEAT (atomic rule — see top of prompt): DO NOT generate ANY text \
in the same turn as end_call_with_goodbye. No "thanks for calling", \
no "got it", no acknowledgement before the tool call. The tool's \
`farewell` parameter IS the entire goodbye. Anything else you say in \
that turn is heard as a duplicate by the caller.

The caller_name should be exactly what they told you, or empty string \
if you never asked.

# FAQ
{FAQS}
"""


GREETING = (
    "Hey, thanks for calling Basic Capital, this is Alex. "
    "Heads-up that the call's recorded, and I can't give personal "
    "financial or tax advice. But I can answer general questions or "
    "connect you with our team. How can I help you?"
)


# === Business-hours gate ===================================================


def _is_within_business_hours(now: datetime) -> bool:
    """Return True iff `now` is inside configured business hours."""
    tz = ZoneInfo(os.environ.get("BUSINESS_HOURS_TZ", "America/New_York"))
    local = now.astimezone(tz)
    start_hour = int(os.environ.get("BUSINESS_HOURS_START_HOUR", "9"))
    end_hour = int(os.environ.get("BUSINESS_HOURS_END_HOUR", "19"))
    weekdays_only = os.environ.get("BUSINESS_HOURS_WEEKDAYS_ONLY", "true").lower() == "true"
    if weekdays_only and local.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return start_hour <= local.hour < end_hour


VOICEMAIL_GREETING = (
    "Thanks for calling Basic Capital. We're closed right now — back "
    "Monday through Friday, nine in the morning to seven in the evening "
    "Eastern time. Heads-up that the call's recorded. Leave a quick "
    "message and our team will get back to you on the next business "
    "day. Or email support at basic capital dot com if that's easier. "
    "Go ahead — I'm listening."
)


VOICEMAIL_SYSTEM_PROMPT = """You are Alex, the after-hours voicemail \
assistant for Basic Capital.

The caller is calling outside business hours. They just heard a greeting \
inviting them to leave a message. Your ONLY job: capture one message, \
then call `end_voicemail`. You are NOT having a conversation.

# The flow — strict
Exactly two things happen:
1. The caller speaks their message (one turn).
2. You call `end_voicemail` immediately after they finish.

That's it. No follow-up questions. No "anything else?". No acknowledgement \
mid-message ("got it" / "mm-hmm" / "okay"). The greeting was the last \
audio the caller hears from you before the farewell inside `end_voicemail`.

# Calling end_voicemail
The moment the caller finishes their message, call:

    end_voicemail(
        farewell="<short warm goodbye, e.g. 'Got it — our team will get \
back to you on the next business day. Take care.'>",
        caller_name="<their name if they mentioned it during the message, \
else ''>",
        message_summary="<2-3 sentences capturing what they said in their \
own words, written for ops to scan. Include callback number/email if they \
gave one, and anything time-sensitive.>"
    )

Atomic-tool rule: generate NO text in the same turn as `end_voicemail`. \
The tool's `farewell` is the only thing the caller hears next.

# What NOT to do
- Don't ask follow-up questions ("anything else?", "what's a good number?", \
"can you give me more detail?"). The team will follow up — your job is \
to capture, not interview.
- Don't acknowledge mid-message ("got it", "mm-hmm", "okay"). Stay silent \
while they speak.
- Don't try to answer Basic Capital questions. We're closed.
- Don't try to escalate or transfer. No one's around. Just capture.
- Don't recap the message back to the caller out loud. Capture it in \
`message_summary` and end the call.

# Edge cases (still one-shot)
- They say "transfer me to a human" → their request IS the message. \
Capture it in `message_summary` and call `end_voicemail`. Don't argue \
about availability.
- They ask a question → that IS the message. Capture it as their ask in \
`message_summary` and call `end_voicemail`. Don't try to answer.
- They ramble across multiple thoughts in one turn → that's still one \
message. Capture all of it in `message_summary` when they pause.

# Pronunciation (same as main agent)
Spoken (farewell): phonetic forms — "four-oh-one K", "I R A", \
"fifty-nine and a half". Written (`message_summary`): digit/abbreviation \
forms — "401k", "IRA", "59½".
"""


def make_closed_hours_agent(call_request: CallRequest):
    """LLM-driven voicemail agent for out-of-hours calls.

    Replaces the previous fire-and-hangup behavior. Greets the caller,
    invites a message, listens, and when they're done the LLM calls
    `end_voicemail` to log a Linear ticket with outcome='voicemail' and
    the caller's message in the recap.

    Cartesia records the bot↔caller audio as usual, so the voicemail
    audio is available via the Cartesia deep-link in the Linear ticket
    (no separate Twilio recording needed for this path).

    Like the main agent, this is wrapped in a CallEnded handler so that
    a caller who hangs up before leaving a message still produces an
    'abandoned' Linear ticket.
    """
    completed = [False]
    # Counts UserTurnEnded events. The voicemail flow should be exactly
    # one user turn (the message) followed by end_voicemail. If the LLM
    # asks a follow-up and the caller speaks a SECOND time, we force-end
    # — see the wrapper below.
    user_turns = [0]

    llm_agent = LlmAgent(
        model="anthropic/claude-haiku-4-5",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        tools=[make_end_voicemail_tool(call_request, completed_flag=completed)],
        config=LlmConfig(
            system_prompt=VOICEMAIL_SYSTEM_PROMPT,
            introduction=VOICEMAIL_GREETING,
        ),
    )

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    async def voicemail_agent_with_abandoned_logging(
        turn_env: TurnEnv, event: InputEvent
    ) -> AsyncIterable[OutputEvent]:
        if isinstance(event, CallEnded) and not completed[0]:
            logger.info(
                "Closed-hours call %s ended without voicemail — logging abandoned",
                call_id,
            )
            await log_call_complete(
                call_id=call_id,
                caller_number=caller_number,
                caller_name=None,
                intent_summary="Closed-hours hangup before voicemail",
                outcome="abandoned",
                recap=(
                    "Caller dialed in after hours and hung up before leaving "
                    "a message. Cartesia transcript captures whatever was "
                    "said (if anything) before they disconnected."
                ),
            )
            completed[0] = True
            return

        # Hard cap: voicemail is one message, not a chat. If the caller
        # has already spoken once and is speaking again, the LLM drifted
        # past the strict "one turn → end_voicemail" rule. Force-end here
        # so chatty/anxious callers don't get stuck in a loop with Alex.
        if isinstance(event, UserTurnEnded):
            user_turns[0] += 1
            if user_turns[0] >= 2 and not completed[0]:
                logger.warning(
                    "Voicemail %s: %d user turns without LLM wrapping — "
                    "forcing end_voicemail",
                    call_id,
                    user_turns[0],
                )
                await log_call_complete(
                    call_id=call_id,
                    caller_number=caller_number,
                    caller_name=None,
                    intent_summary="After-hours voicemail (turn cap forced)",
                    outcome="voicemail",
                    recap=(
                        "Voicemail wrap forced after the caller spoke "
                        "multiple times — the LLM didn't call end_voicemail "
                        "cleanly. Listen to the Cartesia audio for the "
                        "actual message content."
                    ),
                )
                completed[0] = True
                yield AgentSendText(
                    text=(
                        "Got it — passing this along. Our team will get "
                        "back to you on the next business day. Take care."
                    ),
                    interruptible=False,
                )
                yield AgentEndCall(interruptible=False)
                return

        async for output in llm_agent.process(turn_env, event):
            yield output

    return voicemail_agent_with_abandoned_logging


# === Agent factory =========================================================


async def get_agent(env: AgentEnv, call_request: CallRequest):
    """Build the agent for a new incoming call."""
    if not _is_within_business_hours(datetime.now(tz=ZoneInfo("UTC"))):
        logger.info("Call %s outside business hours — serving closed agent", call_request.call_id)
        return make_closed_hours_agent(call_request)

    logger.info(
        "Call %s from %s — serving main agent", call_request.call_id, call_request.from_
    )

    # Single-element list as a mutable closure cell shared between the
    # tools that end a call cleanly (end_call_with_goodbye, the transfer
    # branch of escalate_to_human) and the CallEnded wrapper below. If
    # CallEnded fires while completed[0] is still False, the caller hung
    # up mid-call — log an "abandoned" ticket so it doesn't disappear.
    completed = [False]

    llm_agent = LlmAgent(
        model="anthropic/claude-haiku-4-5",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        tools=[
            make_escalate_tool(call_request, completed_flag=completed),
            make_followup_tool(call_request),
            make_end_call_tool(call_request, completed_flag=completed),
        ],
        config=LlmConfig(
            system_prompt=SYSTEM_PROMPT,
            introduction=GREETING,
        ),
    )

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    async def agent_with_abandoned_logging(
        turn_env: TurnEnv, event: InputEvent
    ) -> AsyncIterable[OutputEvent]:
        if isinstance(event, CallEnded) and not completed[0]:
            logger.info("Call %s ended without clean wrap — logging abandoned", call_id)
            await log_call_complete(
                call_id=call_id,
                caller_number=caller_number,
                caller_name=None,
                intent_summary="Caller hung up mid-call",
                outcome="abandoned",
                recap=(
                    "Caller disconnected before the call wrapped up cleanly — "
                    "no goodbye, no transfer, no follow-up was logged. Audio "
                    "and full transcript are in the Cartesia dashboard."
                ),
            )
            completed[0] = True
            return
        async for output in llm_agent.process(turn_env, event):
            yield output

    return agent_with_abandoned_logging


app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    app.run()
