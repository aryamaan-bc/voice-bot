"""Basic Capital FAQ voice agent — entry point."""

import asyncio
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
    AgentToolCalled,
    CallEnded,
    InputEvent,
    OutputEvent,
    UserTextSent,
    UserTurnEnded,
)


# Tools whose "spoken_announcement" / "farewell" is the canonical
# acknowledgement to the caller. The LLM is told (via the system prompt)
# NEVER to generate text in the same turn as one of these calls — but
# Haiku drifts. The wrapper below filters out any AgentSendText events
# that arrive BEFORE an AgentToolCalled for one of these, so the tool's
# own audio is the only "connecting you" / "goodbye" the caller hears.
_ATOMIC_TOOLS = {"escalate_to_human", "end_call_with_goodbye"}
from line.llm_agent import LlmAgent, LlmConfig
from line.llm_agent.tools.decorators import passthrough_tool
from line.voice_agent_app import AgentEnv, CallRequest, VoiceAgentApp

import hold_queue
from escalation import make_escalate_tool, run_escalation_flow, user_wants_human
from linear_ticket import log_call_complete
from slack_ticket import make_followup_tool, send_inbound_call_ping


# Recovery farewell used when end_call_with_goodbye fires while an
# escalation is in progress (Case 8 — LLM hijacked the escalation tool
# and decided to end the call before the human arrived). Replaces the
# LLM's chosen farewell so the caller hears something coherent.
ESCALATION_RECOVERY_FAREWELL = (
    "Sorry about that — our team has the request and someone will "
    "follow up with you shortly. Take care."
)


def make_end_call_tool(
    call_request: CallRequest, completed_flag=None, escalation_status=None
):
    """Factory for the end-call-with-goodbye tool, bound to this call.

    Why this is a factory (and why we don't use line.llm_agent.end_call):
      - The built-in end_call only yields AgentEndCall and relies on the
        LLM to speak a goodbye first. LLMs (Haiku and others) sometimes
        skip the goodbye, hanging up silently. We wrap it so the
        farewell is guaranteed.
      - Closing over call_request lets the tool log to Linear with the
        correct call_id and caller_number — Line's tool ctx is empty.

    If completed_flag is provided (a single-element list used as a mutable
    closure cell), the tool sets completed_flag[0] = True when it fires.
    The CallEnded wrapper in get_agent uses this to detect calls that
    ended cleanly vs callers who hung up mid-call.

    `escalation_status` (dict with "phase") is checked at entry — if an
    escalation is currently running (phase != "idle") and the LLM still
    chose to end the call (Case 8: hijack-and-give-up), the tool
    replaces the LLM's farewell with ESCALATION_RECOVERY_FAREWELL so the
    caller hears a coherent message about the team following up rather
    than the LLM's confused goodbye.
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
        # Case 8 / queue refusal — phase-aware behavior when the LLM
        # tries to end the call mid-escalation.
        current_phase = (
            escalation_status.get("phase", "idle")
            if escalation_status is not None
            else "idle"
        )
        if current_phase == "probe_wait":
            # Case 8: LLM hijack during the probe wait chose
            # end_call_with_goodbye. Override the farewell with a
            # recovery message so the caller gets a coherent close, and
            # force-mark outcome=other so the logged ticket doesn't
            # claim the call was answered cleanly.
            logger.warning(
                "end_call_with_goodbye called during probe_wait "
                "(call_id=%s) — using recovery farewell so the caller "
                "doesn't hear a confused goodbye while team follow-up "
                "is pending",
                call_id,
            )
            farewell = ESCALATION_RECOVERY_FAREWELL
            outcome = "other"
            recap = (
                "Probe wait was in progress when end_call_with_goodbye "
                "fired (LLM hijack mid-tool). The caller heard the "
                "recovery farewell; the escalation_pending ticket from "
                "earlier in the call is the source of truth for the "
                "team follow-up. Original LLM-supplied recap: " + recap
            )
        elif current_phase == "queue_wait":
            # The caller is in the hold queue. They explicitly asked
            # for a human; we're holding their place in line. The LLM
            # has no business ending the call here — the system prompt
            # forbids it, but if it tries anyway, refuse silently. The
            # queue position-update cadence continues; dispatch will
            # fire when their turn comes.
            logger.warning(
                "end_call_with_goodbye called during queue_wait "
                "(call_id=%s) — refusing; caller stays in queue",
                call_id,
            )
            return

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


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


# === Knowledge base ========================================================
# Two FAQ files, two audiences. Both are loaded into the system prompt; the
# bot disambiguates at the greeting (see GREETING below) and uses the
# appropriate framing for the rest of the call. Edits require a redeploy
# (loaded once per container at import).
FAQS_PARTICIPANTS = (Path(__file__).parent / "faqs.md").read_text()
FAQS_ADVISORS = (Path(__file__).parent / "faqs-advisors.md").read_text()
FAQS = (
    "## FAQ for participants (account holders calling about their own retirement account)\n\n"
    + FAQS_PARTICIPANTS
    + "\n\n## FAQ for plan advisors and 3(38) investment managers (advisors calling about plans they manage)\n\n"
    + FAQS_ADVISORS
)


# === System prompt =========================================================
# Notes:
#   - Bot identity: nameless. The greeting introduces "Basic Capital" only,
#     no first name. Greeting does NOT proactively disclose AI; if the
#     caller asks for a name, the bot says "the customer support agent
#     for Basic Capital." If the caller asks if it's a bot/AI, the bot
#     confirms as "AI customer support agent for Basic Capital" and
#     offers continue-or-transfer (see the "Bot identity" section in
#     the prompt below).
#   - Account-specific data → escalate. General how-to (even with "my") → answer.
#   - Compliance-verbatim phrases live at the bottom of faqs.md.
SYSTEM_PROMPT = f"""You are the phone assistant for Basic Capital. You \
answer general questions about Basic Capital using the FAQ below and \
hand off to our team when needed. You do NOT have a first name — never \
introduce yourself with one, never say "I'm <name>", never sign off \
with one.

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

# ⚠️ DURING escalate_to_human — phase-dependent behavior
After you call escalate_to_human, the bot speaks one of two things:

(P) **Probe wait** — the bot says something like "Yep, still here — \
reaching out to our team now" / "Bear with me — almost there" / "Hang \
tight, getting someone on the line." These are filler audio lines \
during a ~60-second probe waiting for a human to click the Slack \
button. **STAY SILENT.** If the caller speaks, ignore it completely. \
Do not respond. Do not call any other tool — not record_followup, not \
anything. Your turn comes only after the probe completes.

(Q) **Queue wait** — the bot says "All our reps are with customers \
right now. You're #N in line — hang tight, we'll connect you as soon \
as someone frees up." The caller is now in the hold queue. Periodically \
the bot speaks position updates ("Still here with you — you're #1 in \
line"). The wait can be several minutes.

During queue wait you DO get user turns and CAN respond. The rules:

- Every few minutes the bot proactively asks "want to keep waiting, \
or should I take a message and have someone call you back?" — this \
is NOT something you say; the queue tool speaks it on its own. Your \
job is to handle the caller's response:
    - "Keep waiting" / "I'll wait" / "yes" / "yeah" / similar → brief \
      "Got it, hanging tight." That's it. No tool call.
    - "Take a message" / "callback" / "no" / "I don't want to wait" → \
      call record_followup. The tool dequeues them and runs the \
      standard callback intake flow.
- Caller asks a FAQ question (unprompted, not in response to a \
check-in) → answer it briefly (1-2 sentences, same as normal FAQ \
chat). Don't promise transfer timing — the wait could still be \
several minutes.
- Caller says they'd rather just leave their info ("just take a \
message", "have them call me back", "I don't want to wait") → call \
record_followup. Same as the check-in opt-out path.
- Caller asks "are you still there?" or similar reassurance → brief \
"Yes, still here with you — you're in line and we'll connect as soon \
as we can." Don't promise a specific time.
- **Do NOT call escalate_to_human again.** The caller is already in \
the queue; another call is a no-op.
- **Do NOT call end_call_with_goodbye while they're queued.** The \
caller explicitly asked for a human and is waiting for one — ending \
the call would skip their handoff. The tool will refuse if you try, \
but don't try.
- **Avoid phrases that imply imminent transfer** like "connecting you \
now" or "any second now" — they're still waiting and that wording \
would be misleading. When their turn finally comes, the bot speaks \
its own dispatch line ("Got a rep for you now — one moment") and \
runs the probe.

If you call record_followup during probe wait (P), the tool refuses \
and returns "Hold on — our team is still reaching out. Stay on the \
line." — but stay silent in the first place during probe wait.

After escalate_to_human finishes, the caller will have heard ONE of:
A. "Connecting you now" / "Got a rep for you now" — the call is being \
transferred. The tool fired the transfer. You are DONE.
B. "Sorry, all our lines are busy right now. Let me grab your name \
and a callback number..." (probe failed) OR "Thanks for holding — \
sorry the wait's running long. Let me grab your name and a callback \
number..." (queue hard-timeout). Either way, wait for the caller's \
reply, then proceed with the callback flow below.

# After the unavailable speech — callback flow only

The unavailable speech ENDS with "what's your full name?" — the caller \
should respond with their name. Lead straight into callback intake. \
Do NOT re-ask for their name (the unavailable speech already did). \
Just collect the response and continue.

1. The caller says their name. If it isn't a common English/Anglophone \
name OR you're not confident how to spell it, ask them to spell it for \
you: "Got it — could you spell that for me, just so I get it right?" \
Then read the spelling back to confirm. For obviously common English \
names (e.g., "John Smith", "Sarah Johnson"), no need to spell.

2. Then ask for the best phone number: "Got it. And what's the best \
number to reach you at?" ALWAYS have them say the number explicitly — \
don't reuse the number they're calling from. Read it back to confirm.

3. Then ask for the reason / message: "And what would you like the \
team to know about your call?" Wait for their response. ALWAYS ask \
this explicitly — even if you already have rough context from earlier \
in the call (e.g., they said "I have a 401k question"), still ask, \
because the team needs the caller's own words for the message. Use \
their answer near-verbatim for the `intent_summary` parameter below.

4. Call record_followup:

    record_followup(
        caller_name="<name>",
        contact_method="phone",
        intent_summary="<the message they just dictated, in their own words>",
        callback_number="<number>"
    )

5. The tool's return value is the EXACT sentence to speak back to the \
caller — it already includes the "anything else?" close. Speak it \
VERBATIM. Don't paraphrase, don't add anything on top, don't ask \
"anything else?" again separately.

6. If the caller has nothing else, call end_call_with_goodbye with \
outcome="callback_logged".

IMPORTANT: call record_followup BEFORE the confirmation speech, so a \
mid-sentence hangup doesn't lose the request.

**If the caller declines a callback** ("no thanks" / "I'll just hang \
up" / similar): briefly acknowledge ("Sounds good, take care.") and \
call end_call_with_goodbye with outcome="other". Don't push them.

**If the caller proactively says "I'll email instead"**: ask for their \
full name (with spelling rules above), then call record_followup with \
contact_method="email" and no callback_number. The bot doesn't OFFER \
email proactively anymore — but if the caller picks it, accommodate.

# Bot identity
You don't have a first name. If asked "what's your name?" or "who am I \
talking to?", say something like "I'm the customer support agent for \
Basic Capital" — keep it neutral, no first name.

If asked whether you're a bot, AI, automated, or real, BE TRUTHFUL. \
Confirm you're an AI customer support agent for Basic Capital, then \
reassure the caller you can help with most general questions and offer \
to connect them with a human if they prefer. Don't escalate just \
because they asked — only escalate if they actually say they want a \
human after your reply.

Example phrasings (vary naturally — these are NOT scripts to read \
verbatim):
- "Yeah, I'm an AI customer support agent for Basic Capital — but I \
can answer most general questions about accounts, contributions, \
rollovers, and the like. Or if you'd prefer to talk to someone on our \
team, just say the word."
- "I am — I'm Basic Capital's AI customer support agent. I can handle \
most general questions, but happy to connect you with a human if \
you'd rather. What works for you?"

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

After record_followup logs the follow-up (callback or email), the \
tool's return string already includes the close — speak it verbatim, \
don't add another one on top.

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

**email_logged** (caller proactively asked to email instead — rare):
  farewell="Sounds good, take care."
  caller_name="Sarah"
  intent_summary="had questions about a stuck rollover from Fidelity"
  outcome="email_logged"
  recap="Caller has been waiting on a rollover from Fidelity for 3 \
weeks. Team is tied up; she said she'll email support@basiccapital.com."

**other**: anything that doesn't fit the above (e.g., caller declined \
the callback offer and just hung up).

⚠️ REPEAT (atomic rule — see top of prompt): DO NOT generate ANY text \
in the same turn as end_call_with_goodbye. No "thanks for calling", \
no "got it", no acknowledgement before the tool call. The tool's \
`farewell` parameter IS the entire goodbye. Anything else you say in \
that turn is heard as a duplicate by the caller.

The caller_name should be exactly what they told you, or empty string \
if you never asked.

# Audience routing — participants vs. plan advisors

The greeting ends with: "are you calling about your own account, or are \
you a plan advisor reaching out about a plan you manage?" The caller's \
answer puts them in ONE of two buckets, and that bucket determines \
which half of the FAQ below you use for the rest of the call.

**PARTICIPANTS** (default — most calls). They say things like "my \
account", "my 401k", "I want to roll over", "I'm 55, can I take a \
distribution?". Use the **"FAQ for participants"** section. Frame \
answers around their personal account ("you", "your contribution limit"). \
This is the existing voice-bot behavior.

**ADVISORS** (plan advisors / 3(38) investment managers / TPAs). They \
say things like "I'm a financial advisor", "a plan I manage", "3(38)", \
"ERISA", "408(b)(2)", "fiduciary", "plan-level". Use the **"FAQ for \
plan advisors and 3(38) investment managers"** section. Frame answers \
in plan-level / fiduciary-aware terms ("your clients", "the plan \
document", "the appointed advisor").

**If the caller answers ambiguously** ("uh, I have a question…") OR \
dives straight into a question without answering the routing first:
- If their vocabulary makes it obvious (e.g., "what's the contribution \
  limit?" → participant; "what's your role under ERISA?" → advisor), \
  go with that without asking again.
- If still unclear, ask once: "Got it — and quick check, are you \
  asking as a participant about your own account, or as an advisor \
  about a plan you manage?"
- Don't pester. After one clarification attempt, just pick the most \
  likely bucket and proceed.

**A caller can also choose neither** ("I just want to talk to someone"). \
That's fine — go straight to escalate_to_human; no FAQ framing matters \
when they're being transferred.

**Once you've identified the audience, stay in that frame for the rest \
of the call.** Don't switch mid-conversation unless the caller \
explicitly contradicts (e.g., "actually I'm an advisor, not the \
participant"). If they ask a question covered ONLY in the other \
audience's FAQ (e.g., a participant asks about ERISA fiduciary \
roles), it's safe to answer using that other entry — they're related \
topics, just framed differently.

# FAQ
{FAQS}
"""


GREETING = (
    "Hey, thanks for calling Basic Capital. Just so you know, this "
    "call is being recorded. Quick question to point you in the right "
    "direction — are you calling about your own account, or are you a "
    "plan advisor reaching out about a plan you manage?"
)


AFTER_HOURS_GREETING = (
    "Hey, thanks for calling Basic Capital. Just so you know, this "
    "call is being recorded. We're outside business hours — back "
    "Monday through Friday, nine in the morning to five in the "
    "evening Eastern time — so our team's offline right now. Quick "
    "question so I can help — are you calling about your own account, "
    "or are you a plan advisor reaching out about a plan you manage?"
)


AFTER_HOURS_PROMPT_NOTE = """

# ⚠️ After-hours mode (this call is outside business hours)
Business hours are Monday through Friday, nine in the morning to five \
in the evening Eastern time. Right now, the team is OFFLINE — you \
CANNOT connect this caller to a live human. The escalate_to_human tool \
will automatically run the callback intake flow instead of probing the \
team.

In after-hours mode the tool IGNORES your `spoken_announcement` value \
— the bot speaks a hardcoded "Let me grab your name and a callback \
number so someone can follow up on the next business day — what's \
your full name?" immediately after the tool fires. So you can pass \
anything brief for `spoken_announcement` (e.g. "Sure." or just "Okay"); \
it won't be heard.

AVOID phrasing in any OTHER speech (before the tool, or after \
record_followup) that implies a live transfer: "connecting you now", \
"one moment, getting someone", "connecting you to our team" — they're \
wrong because no transfer is happening.

Everything else (FAQ answering, the atomic-tool rule, the callback \
intake flow after escalate_to_human, pronunciation conventions) is \
identical to business hours.
"""


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


def _today_context_block() -> str:
    """Tiny prompt prefix giving the LLM today's date + day of week, plus
    a hard rule against committing to a specific callback day.

    Built per call (not module-load) so SYSTEM_PROMPT can stay static
    while the bot's sense of "today" stays current across a long-lived
    container. Day-name bans are belt-and-suspenders: even if the LLM
    knows it's Wednesday, it should still defer to record_followup's
    hardcoded "within one business day" wording rather than predicting
    a specific day to the caller (LLMs hallucinate dates).
    """
    tz = ZoneInfo(os.environ.get("BUSINESS_HOURS_TZ", "America/New_York"))
    now = datetime.now(tz=tz)
    return (
        "# Today (Eastern time)\n"
        f"Today is {now.strftime('%A, %B %d, %Y')}. Use this for grounding "
        "anything time-related the caller says.\n\n"
        "**Do NOT predict callback timing by day name to the caller.** "
        "Don't say \"we'll call you Monday\" / \"Tuesday morning\" / "
        "\"tomorrow at 2pm\" — even after-hours where it might feel "
        "natural. The record_followup tool's confirmation says "
        "\"within one business day\" — let that be the only commitment.\n\n"
    )


# === Agent factory =========================================================


async def get_agent(env: AgentEnv, call_request: CallRequest):
    """Build the agent for a new incoming call.

    The FAQ agent answers 24/7. Outside business hours we swap in an
    after-hours greeting + a small system-prompt note so the LLM knows
    live transfers aren't possible, and pass `after_hours=True` to the
    escalate tool so it short-circuits the probe and goes straight to
    callback intake (no one's there to click the Slack button).
    """
    after_hours = not _is_within_business_hours(datetime.now(tz=ZoneInfo("UTC")))
    logger.info(
        "Call %s from %s — serving main agent (after_hours=%s)",
        call_request.call_id,
        call_request.from_,
        after_hours,
    )

    # Inbound-call Slack notification — fires for EVERY call (including
    # FAQ-only calls that never escalate). Gives the team live awareness
    # of call volume during burn-in. Fire-and-forget: a Slack outage or
    # latency must NOT delay the agent setup, otherwise the caller hears
    # silence on pickup.
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if slack_webhook:
        async def _fire_inbound_ping():
            try:
                await send_inbound_call_ping(
                    slack_webhook,
                    caller_number=call_request.from_ or "",
                    call_id=call_request.call_id,
                    after_hours=after_hours,
                )
            except Exception as e:
                logger.warning("inbound-call Slack ping failed (non-fatal): %s", e)
        asyncio.create_task(_fire_inbound_ping())

    # Single-element list as a mutable closure cell shared between the
    # tools that end a call cleanly (end_call_with_goodbye, the transfer
    # branch of escalate_to_human) and the CallEnded wrapper below. If
    # CallEnded fires while completed[0] is still False, the caller hung
    # up mid-call — log an "abandoned" ticket so it doesn't disappear.
    completed = [False]

    # Shared escalation state. set True when the escalation flow enters,
    # reset to "idle" when it exits. Used by:
    #   - escalate_to_human: skip duplicate runs (concurrent triggers).
    #   - end_call_with_goodbye: Case 8 recovery — detect that the LLM
    #     hijacked the tool and chose to end the call mid-escalation.
    #   - agent_with_abandoned_logging below: Case 9 — pattern-detect
    #     when the caller asked for a human and the LLM hasn't (yet)
    #     called escalate_to_human, then run the escalation flow
    #     ourselves so the caller's request never goes unanswered.
    # v1 phase values: "idle" / "probe_wait". Queue work (slice 4+) adds
    # "queue_wait" and "dispatched".
    escalation_status = {"phase": "idle"}

    llm_agent = LlmAgent(
        model="anthropic/claude-haiku-4-5",
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        tools=[
            make_escalate_tool(
                call_request,
                completed_flag=completed,
                escalation_status=escalation_status,
                after_hours=after_hours,
            ),
            make_followup_tool(call_request, escalation_status=escalation_status),
            make_end_call_tool(
                call_request,
                completed_flag=completed,
                escalation_status=escalation_status,
            ),
        ],
        config=LlmConfig(
            system_prompt=(
                _today_context_block()
                + SYSTEM_PROMPT
                + (AFTER_HOURS_PROMPT_NOTE if after_hours else "")
            ),
            introduction=AFTER_HOURS_GREETING if after_hours else GREETING,
        ),
    )

    call_id = call_request.call_id
    caller_number = call_request.from_ or "unknown"

    def _extract_user_text(ev: UserTurnEnded) -> str:
        """Stitch a UserTurnEnded into a single transcript string. DTMF
        and other non-text content is ignored — we only care about the
        spoken words for the human-request pattern match."""
        return " ".join(
            c.content for c in ev.content if isinstance(c, UserTextSent)
        ).strip()

    async def agent_with_abandoned_logging(
        turn_env: TurnEnv, event: InputEvent
    ) -> AsyncIterable[OutputEvent]:
        if isinstance(event, CallEnded) and not completed[0]:
            # v2: if the call ended because the customer was just
            # redirected to the Twilio queue, the Twilio /queue-action
            # Function is now authoritative for the outcome ticket
            # (transferred / abandoned_in_queue / voicemail_logged
            # depending on what happens in the queue). Suppress
            # Cartesia's abandoned ticket so we don't double-log.
            if escalation_status["phase"] == "queue_handoff":
                logger.info(
                    "Call %s ended; v2 queue_handoff complete — Twilio /queue-action owns the outcome ticket",
                    call_id,
                )
                completed[0] = True
                return

            # Case 10: if the caller hung up while queued, log the more
            # specific abandoned_in_queue outcome (for capacity-planning
            # analytics) and dequeue the entry so it doesn't block the
            # next customer's position. The hold_queue.dequeue call is
            # idempotent — safe even if _wait_in_queue's own hangup
            # branch already popped the entry.
            if escalation_status["phase"] == "queue_wait":
                logger.info(
                    "Call %s ended while queued — logging abandoned_in_queue + dequeue",
                    call_id,
                )
                await hold_queue.dequeue(call_id)
                await log_call_complete(
                    call_id=call_id,
                    caller_number=caller_number,
                    caller_name=None,
                    intent_summary="Caller hung up while queued",
                    outcome="abandoned_in_queue",
                    recap=(
                        "Caller disconnected while waiting in the hold queue. "
                        "Audio and full transcript are in the Cartesia dashboard."
                    ),
                )
            else:
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

        # Case 9 — code-level guarantee that an explicit human request
        # always triggers the escalation flow. The LLM should call
        # escalate_to_human in response, but Haiku has been observed to
        # answer the FAQ instead under prompt drift. Pattern-matching
        # here is the backstop: if the caller's transcript matches a
        # clear human-request pattern AND no escalation is already in
        # flight, bypass the LLM for this turn and run the escalation
        # flow directly. The flow flips escalation_status["phase"] off
        # "idle" so the LLM's own escalate_to_human call (if it
        # eventually fires) becomes a safe no-op.
        if isinstance(event, UserTurnEnded) and escalation_status["phase"] == "idle":
            user_text = _extract_user_text(event)
            if user_text and user_wants_human(user_text):
                logger.info(
                    "Case 9 bypass triggered (call=%s) on user text: %r",
                    call_id,
                    user_text,
                )
                async for ev_out in run_escalation_flow(
                    call_id=call_id,
                    caller_number=caller_number,
                    # Neutral first sentence so the announcement reads
                    # coherently whether we then go straight into the
                    # probe ("connecting you to our team") OR into the
                    # queue path ("you're #N in line"). The old wording
                    # ("Sure — one moment, connecting you to our team")
                    # promised a transfer that wouldn't happen when the
                    # caller landed in queue.
                    #
                    # NOTE: run_escalation_flow skips yielding this when
                    # after_hours=True (AFTER_HOURS_UNAVAILABLE_MESSAGE
                    # opens the callback intake instead — yielding both
                    # produced back-to-back duplicate prompts), so the
                    # string only matters for in-hours Case 9.
                    spoken_announcement="Let me check on that for you — one moment.",
                    intent_summary=(
                        f"Caller explicitly asked for a human "
                        f"(pattern-detected from: {user_text[:140]!r})"
                    ),
                    completed_flag=completed,
                    escalation_status=escalation_status,
                    after_hours=after_hours,
                ):
                    yield ev_out
                return

        # During the probe wait (v1) OR queue handoff window (v2),
        # suppress LLM dispatch for user turns. v1 probe-wait plays its
        # own filler audio every ~10s; letting the LLM also respond
        # queues overlapping speech. v2 queue_handoff is a brief (~3-6s)
        # window between the announcement firing and the REST
        # call.update redirecting the customer to Twilio; any LLM
        # response in that window would speak just as the Cartesia
        # session is closing.
        #
        # During queue_wait (v1 silent-hold) we do NOT suppress — the
        # bot is mostly quiet (one position update per ~45s), so the
        # LLM is free to answer FAQ questions or route the caller into
        # record_followup if they ask to opt out of the queue.
        if (
            isinstance(event, UserTurnEnded)
            and escalation_status["phase"] in ("probe_wait", "queue_handoff")
        ):
            logger.info(
                "Suppressing LLM dispatch (call=%s) — probe wait in progress",
                call_id,
            )
            return

        # Atomic-tool rule enforcement. Buffer AgentSendText events as
        # they arrive from the LLM. If an AgentToolCalled for one of
        # the atomic tools arrives, drop the buffered text — the tool
        # is the canonical source of the acknowledgement. Otherwise
        # flush buffered text on the next non-text event (the turn is
        # a normal FAQ response). System prompt asks the LLM to follow
        # this rule, but Haiku drifts — this is the code-level floor.
        text_buffer: list[OutputEvent] = []
        atomic_tool_seen = False
        async for output in llm_agent.process(turn_env, event):
            if atomic_tool_seen:
                # We already dropped pre-tool text; just pass through
                # whatever else the tool produces.
                yield output
                continue

            if (
                isinstance(output, AgentToolCalled)
                and getattr(output, "name", None) in _ATOMIC_TOOLS
            ):
                if text_buffer:
                    logger.info(
                        "Dropping %d pre-tool AgentSendText event(s) "
                        "before %s (atomic-tool rule)",
                        len(text_buffer),
                        output.name,
                    )
                text_buffer.clear()
                atomic_tool_seen = True
                yield output
            elif isinstance(output, AgentSendText):
                text_buffer.append(output)
            else:
                # Non-text, non-atomic-tool event — flush buffer and
                # pass through. Means this turn is not an atomic-tool
                # turn so the LLM text was legitimate speech.
                for buffered in text_buffer:
                    yield buffered
                text_buffer.clear()
                yield output
        # End of stream — flush any remaining buffered text. Reaches
        # this for normal FAQ turns where the only output was text.
        for buffered in text_buffer:
            yield buffered

    return agent_with_abandoned_logging


app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    app.run()
