# Working on this codebase

Project-specific notes for Claude (or any AI agent) editing this voice bot.
Public-facing setup docs live in [README.md](README.md). This file is for
behavior, gotchas, and rules that aren't obvious from the code.

## Architecture in one screen

- **Entry point**: `get_agent` in [main.py](main.py) returns either:
  - **Closed-hours voicemail agent** — LLM-driven, single tool (`end_voicemail`),
    strict one-turn capture flow with a force-end safety net.
  - **Main FAQ agent** — full `LlmAgent` with three tools, wrapped in a
    `CallEnded` handler that catches mid-call hangups as `abandoned` tickets.
- **Three tools for the main agent**, all factory-built per call so they can
  close over `call_request.call_id` and `call_request.from_` (Line's tool `ctx`
  is empty):
  1. `escalate_to_human` ([escalation.py](escalation.py)) — `passthrough_tool`.
     Announces → Slack-pings team → Twilio probe (with periodic filler audio
     every 10s to mask probe silence) → outcome speech entirely inside the
     tool. The LLM calls it and goes silent.
  2. `record_followup` ([slack_ticket.py](slack_ticket.py)) — `loopback_tool`
     for the unavailable path. Logs the callback request (default) or
     email follow-up (if the caller proactively asks) to Slack; returns
     the exact verbatim string for the LLM to speak back.
  3. `end_call_with_goodbye` ([main.py](main.py)) — `passthrough_tool`. Logs
     to Linear + Slack, then speaks the farewell, then hangs up. Every clean
     call wrap-up goes through this.
- **One tool for the voicemail agent**:
  - `end_voicemail` ([main.py](main.py)) — same shape as
    `end_call_with_goodbye` but with three params (`farewell`, `caller_name`,
    `message_summary`) instead of five; hardcodes `outcome="voicemail"`.
- **Per-call ticketing**: `log_call_complete` in
  [linear_ticket.py](linear_ticket.py) is the single entry point. Called from
  every termination path. Outcomes: `answered_from_faq | callback_logged |
  email_logged | transferred | closed_hours | voicemail | abandoned | other`.
- **Post-handoff recording**: when a call transfers, Cartesia is out of the
  loop. Twilio records the conference (via `conference-join.js` Function),
  and `recording-callback.js` posts a Slack DM with the listen link when the
  recording is ready.

## The atomic-tool rule (the #1 bug source)

Three tools speak directly to the caller: `escalate_to_human`,
`end_call_with_goodbye`, and `end_voicemail`. When the LLM calls any of them,
it MUST NOT generate any text in the same turn — the tool produces all the
speech.

If the LLM emits text alongside the tool call, the caller hears two
back-to-back messages and it sounds broken. The rule appears multiple times
in the system prompt for a reason — Haiku regresses on it under prompt
drift. Don't remove the reinforcements. If the bot starts double-speaking,
the prompt has likely been edited; check the atomic-rule blocks first.

## Spoken vs written forms

TTS mispronounces digits. The FAQ uses phonetic forms because that text gets
spoken ("four-oh-one K", "ten ninety-nine R", "fifty-nine and a half"). But
Linear tickets and Slack DMs are READ by humans — those need digit forms
("401k", "1099-R", "59½"). The system prompt has a table; preserve it when
editing.

When writing a new FAQ entry: use phonetic forms in the answer text. When
adjusting the system prompt's tool-call examples: spoken text fields
(`farewell`, `spoken_announcement`) use phonetic; logged fields
(`intent_summary`, `recap`, `caller_name`, `message_summary`) use digit form.

## AI disclosure policy (REACTIVE, not proactive)

- **Greeting**: does NOT mention "AI", "bot", or "automated". Alex introduces
  by name only.
- **If the caller asks** ("are you a bot?", "is this AI?"): Alex MUST be
  truthful and confirm. Identify specifically as
  **"AI customer support specialist for Basic Capital"** (NOT "voice
  assistant" — user corrected this). Then offer to keep helping OR transfer
  to a human. Only escalate if the caller actually picks the human path.

This was reversed from an earlier policy on 2026-05-01. If you're tempted to
"clean up" the Bot Identity section by routing AI-questions back to
escalation, don't — the user explicitly chose this behavior.

## Empathy carve-out — frustrated callers

For most heavy topics (job loss, hardship, market losses), the prompt allows
one brief beat of acknowledgement before the answer. **But for frustrated /
shouting / "REPRESENTATIVE REPRESENTATIVE" callers, NO empathy preamble.**
The bot must just say "One moment." and trigger the probe immediately. The
faster the bot gets out of the way, the better. Don't soften this rule —
empathy on top of agitation makes callers angrier.

## Voicemail flow is strict

After-hours calls go to a separate LLM agent with a strict one-turn capture
flow:
- Greeting → caller speaks one message → bot calls `end_voicemail`. Done.
- NO acknowledgements, NO follow-up questions, NO "anything else?"
- Edge cases (caller asks a question, wants a human, transfers requested) are
  all framed as "that IS the message — capture it and end."
- A wrapper in `make_closed_hours_agent` force-ends the call if the caller
  speaks a second time, to prevent LLM drift into chatty mode.

If you're tempted to make the voicemail agent more conversational, don't —
the user explicitly chose strict mode (2026-05-11). One message, then end.

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

## Twilio Functions (post-handoff recording)

The `twilio_functions/` directory is the **source of truth**; Twilio's
hosted copies must match. Edit here, paste into Twilio Console, redeploy.

Two Functions in the Service `bc-voice-functions`:
- `/conference-join` — TwiML for the conference-join number, enables
  recording, sets up the recording callback URL.
- `/recording-callback` — receives Twilio's `recordingStatusCallback` POST
  when the recording is ready, posts a Slack DM with caller number,
  duration, and listen link.

Both must be **Public visibility** in the Twilio Console (top-right of the
editor) — without this, Twilio's voice network can't hit them.

`SLACK_WEBHOOK_URL` must be set in the **Service's environment variables**
(separate from your project `.env` — Twilio Functions can't read your `.env`).

## Twilio account must be Full (not Trial)

Trial Twilio accounts reject inbound transfers from unverified caller IDs
with error 21264. This breaks the customer-to-conference transfer step.
Upgrade is required for production. If you see calls that "ring then fail
instantly with 0s duration" in the Twilio call log, check the account type
via `curl -u $TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN
https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID.json` — look
for `"type": "Full"`.

## Deploy workflow gotcha

`cartesia deploy` pushes **code only**.
`cartesia env set --from=.env --agent-id=<id>` pushes **env vars only**.

They're separate steps. If you change `.env`, you must run both for
production to pick up the changes. This bit us once — the agent was
running with stale `DEMO_MODE=true` after a `cartesia deploy` because env
vars weren't pushed.

## Demo mode

`DEMO_MODE=true` makes `escalate_to_human` skip Twilio entirely (5-second
sleep → "unavailable") so the unavailable-path / callback flow can be
exercised in the browser playground without a phone line. Slack and Linear
still fire normally. The demo mode flag is read fresh on each escalation
call, so you can flip it without redeploying code — but you do need to
`cartesia env set` for production to see the change.

## Testing tips

- Phone path (real Twilio number) is the reliable one for end-to-end testing.
- Browser playground (`cartesia chat 8000`) has occasional WebRTC stalls
  ("Active 0:00:00") on some networks — not your code, just Cartesia infra
  flakiness. Switch to a phone hotspot or skip the browser and test via the
  phone number.
- Watch `cartesia logs --follow` during a call — the per-tool logs
  (`escalate_to_human START`, `Linear ticket BC-xxx created`, `Slack
  post-call summary sent`) tell you which paths fired.
- For after-hours testing, temporarily flip `BUSINESS_HOURS_START_HOUR` /
  `BUSINESS_HOURS_END_HOUR` in `.env`, then `cartesia env set --from=.env`.
- Twilio call records: `curl -u $TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN
  https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/Calls.json
  | jq` — useful for diagnosing transfer failures.

## Style for prose in this repo

The user prefers terse, direct comms — no headers/sections for short answers,
no end-of-turn recaps when the diff is self-explanatory, and code comments
only when the *why* is non-obvious. The existing prompt blocks and
docstrings are deliberate — match that voice.
