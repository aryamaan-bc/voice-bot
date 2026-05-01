# Working on this codebase

Project-specific notes for Claude (or any AI agent) editing this voice bot.
Public-facing setup docs live in [README.md](README.md). This file is for
behavior, gotchas, and the rules that aren't obvious from the code.

## Architecture in one screen

- **Entry point**: `get_agent` in [main.py](main.py) returns either:
  - `make_closed_hours_agent` (no LLM, plays recorded message + logs ticket)
  - A wrapper around `LlmAgent` that also intercepts `CallEnded` to log
    abandoned calls (caller hung up mid-conversation)
- **Three tools**, all factory-built per call so they can close over
  `call_request.call_id` and `call_request.from_` (Line's tool `ctx` is empty):
  1. `escalate_to_human` ([escalation.py](escalation.py)) — `passthrough_tool`,
     handles announce / Slack-ping / Twilio-conference probe / outcome speech
     entirely inside the tool. The LLM calls it and goes silent.
  2. `record_followup` ([slack_ticket.py](slack_ticket.py)) — `loopback_tool`
     for the unavailable path. Logs callback or email request to Slack;
     returns the exact verbatim string for the LLM to speak back.
  3. `end_call_with_goodbye` ([main.py](main.py)) — `passthrough_tool`. Logs
     the call to Linear + Slack, then speaks the farewell, then hangs up.
     Every clean call wrap-up goes through this.
- **Per-call ticketing**: `log_call_complete` in
  [linear_ticket.py](linear_ticket.py) is the single entry point. Called from
  every termination path (clean goodbye, transfer, closed-hours, abandoned).
  Outcomes: `answered_from_faq | callback_logged | email_logged | transferred
  | closed_hours | abandoned | other`.

## The atomic-tool rule (the #1 bug source)

Two tools speak directly to the caller: `escalate_to_human` and
`end_call_with_goodbye`. When the LLM calls either, it MUST NOT generate any
text in the same turn — the tool produces all the speech.

If the LLM emits text alongside the tool call, the caller hears two
back-to-back messages and it sounds broken. This rule appears three times in
the system prompt for a reason — Haiku regresses on it under prompt drift.
Don't remove the reinforcements. If the bot starts double-speaking, the
prompt has likely been edited; check the atomic-rule blocks first.

## Spoken vs written forms

TTS mispronounces digits. The FAQ uses phonetic forms because that text gets
spoken ("four-oh-one K", "ten ninety-nine R", "fifty-nine and a half"). But
Linear tickets and Slack DMs are READ by humans — those need digit forms
("401k", "1099-R", "59½"). The system prompt has a table; preserve it when
editing.

When writing a new FAQ entry: use phonetic forms in the answer text. When
adjusting the system prompt's tool-call examples: spoken text fields
(`farewell`, `spoken_announcement`) use phonetic; logged fields
(`intent_summary`, `recap`, `caller_name`) use digit form.

## AI disclosure policy (REACTIVE, not proactive)

- **Greeting**: does NOT mention "AI", "bot", or "automated". Alex introduces
  by name only. Don't add proactive disclosure to the greeting.
- **If the caller asks** ("are you a bot?", "is this AI?"): Alex MUST be
  truthful and confirm. Identify specifically as
  **"AI customer support specialist for Basic Capital"** (NOT "voice
  assistant" — user corrected this). Then offer to keep helping OR transfer
  to a human. Only escalate if the caller actually picks the human path.

This was reversed from an earlier policy on 2026-05-01. If you're tempted to
"clean up" the Bot Identity section by routing AI-questions back to
escalation, don't — the user explicitly chose this behavior.

## IRS rules vs BC operational capability

Multiple FAQs warn that some IRS-allowed actions are NOT yet built on BC's
platform. Before drafting or approving any FAQ involving rollovers,
contributions, transfers, or distributions, ask:

> Is this just the IRS-allowed pattern, or does BC actually have the
> operational ability to process this today?

Known gaps (write FAQs in terms of these, not just IRS rules):
- Prior-year contributions — not supported
- Incoming rollovers into BC's 401(k) — not supported
- ACAT transfers — not supported
- In-kind incoming rollovers — not supported (cash only)
- 401(k) loans / hardship loans — not offered
- Catch-up contributions (50+) — IRS allows; BC doesn't process yet
- Retirement Mortgage explanations — legacy product, sunsetting, routes to
  humans (don't explain LLC structure, 4x financing, "preferred equity"
  language — those phrases are off-limits for the bot)

If you spot a new gap, append to
`~/.claude/projects/-Users-aryamaanlakhotia-Downloads-voice-bot1/memory/feedback_faq_irs_vs_bc.md`.

## Editing config files

Use `Edit` (exact-string match) for `.env`, `cartesia.toml`,
`pyproject.toml`. Never `sed -i` on these — a sed range pattern silently
deleted `ANTHROPIC_API_KEY` from `.env` once. The risk is that sed's pattern
matching is line-based and can over-match silently; Edit fails loudly when
the string isn't unique.

## FAQ edits

`faqs.md` is loaded once at container start, so any change requires
`cartesia deploy` to take effect. Section headers (`# …`) are for human
organization but the LLM still reads the whole file — write headers that
help future maintainers, not headers that try to scope the LLM.

Compliance phrasings live at the bottom under "Legal-approved phrasings" —
the system prompt instructs the LLM to use them VERBATIM. Don't paraphrase
or move them inline.

## Cartesia deep-links

The format that actually works in the dashboard:
```
https://play.cartesia.ai/agents/{CARTESIA_AGENT_ID}?tab=calls&call=ac_sid_xxx
```

Query params, not path segments. The call ID needs the `ac_` prefix that
Cartesia adds in the dashboard but isn't in the `CallRequest.call_id` we
receive. [linear_ticket.py](linear_ticket.py) auto-prepends it. If you
build a new place that surfaces this URL, prepend `ac_` if missing.

## Demo mode

`DEMO_MODE=true` makes `escalate_to_human` skip Twilio entirely (5-second
sleep → "unavailable") so the unavailable-path / callback flow can be
exercised in the browser playground without a phone line. Slack and Linear
still fire normally. The demo mode flag is read fresh on each escalation
call, so you can flip it without redeploying as long as the container picks
up the new env (in practice that's a deploy too).

## Testing tips

- Phone path (real Twilio number) is the reliable one for end-to-end testing.
- Browser playground (`cartesia chat 8000`) has occasional WebRTC stalls
  ("Active 0:00:00") — not your code, just Cartesia infra flakiness. Don't
  chase it as a bug; restart the playground or move to phone testing.
- Watch `cartesia logs --follow` during a call — the per-tool logs
  (`escalate_to_human START`, `Linear ticket BC-xxx created`, `Slack
  post-call summary sent`) tell you which paths fired.

## Style for prose in this repo

The user prefers terse, direct comms — no headers/sections for short answers,
no end-of-turn recaps when the diff is self-explanatory, and code comments
only when the *why* is non-obvious. The existing prompt blocks and
docstrings are deliberate — match that voice.
