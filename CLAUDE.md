# Working on this codebase

Project-specific notes for Claude (or any AI agent) editing this voice bot.
Public-facing setup docs live in [README.md](README.md). This file is for
behavior, gotchas, and rules that aren't obvious from the code.

## Architecture in one screen

- **Public number**: `+1 (888) 460-4901` (toll-free). Imported into Cartesia
  via the Twilio integration (see "Toll-free import flow" below). Cartesia
  handles the Twilio webhook wiring — do NOT set the toll-free's voice URL
  by hand.
- **Entry point**: `get_agent` in [main.py](main.py) always returns the
  **Main FAQ agent** — full `LlmAgent` with three tools, wrapped in
  `agent_with_abandoned_logging` which handles BOTH the `CallEnded`
  abandoned-ticket fallback AND the **Case 9 pre-LLM bypass**: if the user
  transcript matches `user_wants_human()` and no escalation is in progress,
  the wrapper runs the escalation flow directly without invoking the LLM.
  Outside business hours, the greeting + a small prompt note are swapped in
  and `after_hours=True` is threaded through to `escalate_to_human` — the
  FAQ flow itself is unchanged.
- **Per-call shared state** (lives in `get_agent`, passed to tool factories):
  - `completed = [False]` — set True when any clean termination fires (used
    by `CallEnded` to skip the duplicate abandoned ticket).
  - `escalation_status = {"in_progress": False}` — set True the moment
    `run_escalation_flow` enters, cleared in `finally`. Used by
    `escalate_to_human` to skip duplicate runs and by `end_call_with_goodbye`
    to detect Case 8 (LLM hijack ending the call mid-escalation).
- **Three tools for the main agent**, all factory-built per call so they can
  close over `call_request.call_id` and `call_request.from_` (Line's tool
  `ctx` is empty):
  1. `escalate_to_human` ([escalation.py](escalation.py)) — `passthrough_tool`.
     Delegates to `run_escalation_flow()`, which is also called directly from
     the main.py wrapper for the Case 9 bypass. Single source of truth for
     the escalation behavior.
  2. `record_followup` ([slack_ticket.py](slack_ticket.py)) — `loopback_tool`
     for the unavailable path. Logs the callback request (default) or
     email follow-up (if the caller proactively asks) to Slack; returns
     the exact verbatim string for the LLM to speak back.
  3. `end_call_with_goodbye` ([main.py](main.py)) — `passthrough_tool`. Checks
     `escalation_status["in_progress"]` at entry; if active, replaces the
     LLM's farewell with `ESCALATION_RECOVERY_FAREWELL` (Case 8). Logs to
     Linear + Slack, speaks farewell, hangs up.
- **Per-call ticketing**: `log_call_complete` in
  [linear_ticket.py](linear_ticket.py) is the unified entry for outcome
  tickets. `log_escalation_started` fires the moment an escalation begins
  (intentionally duplicates with the final-outcome ticket — guarantees a
  paper trail even when the rest of the call falls apart silently). Outcomes:
  `answered_from_faq | callback_logged | email_logged | transferred |
  closed_hours | voicemail | abandoned | escalation_pending | other`.
- **Browser pickup (the human's side)**: when escalation fires, the Slack
  ping has a "Take call in browser" button URL pointing at
  `agent-pickup.html` with `?conf=<conf>&customer=<E.164>&intent=<short>`.
  The page loads Twilio Voice JS SDK from unpkg, fetches an Access Token
  from `/agent-token`, dials into the conference via `/agent-dial`. A 10s
  watchdog flips a red urgent banner with the customer's phone number if
  the WebRTC handshake stalls (Case 6 recovery).
- **Post-handoff recording**: when a call transfers, Cartesia is out of the
  loop. Twilio records the conference (via `conference-join.js` Function),
  and `recording-callback.js` posts a Slack DM with the listen link when the
  recording is ready.

## LLM-hijack recovery (Cases 1-9) — the core production-safety story

Cartesia's voice agent is real-time bidirectional: the customer's mic is
always live, feeding STT into the LLM. There is no Line-SDK API to mute
user audio during a passthrough tool. If the customer speaks during the
silent gap between filler-audio lines, the LLM can be invoked and
"hijack" the in-progress escalate_to_human tool — generating a
conversational response that derails the escalation. Four layered
defenses, all in [escalation.py](escalation.py) + [main.py](main.py):

1. **LLM dispatch suppression during escalation** ([main.py](main.py)).
   The `agent_with_abandoned_logging` wrapper checks
   `escalation_status["in_progress"]` on each `UserTurnEnded` event and
   refuses to forward the turn to the LLM while an escalation is active.
   With the LLM unreachable, user mic input during a filler gap can't
   trigger a hijack at all. Added in commit d5410ff and currently the
   primary defense — the other three below are belt-and-suspenders.
2. **Filler audio** ([escalation.py](escalation.py)).
   `PROBE_FILLER_INTERVAL_SECONDS = 10`, 14 lines in `PROBE_FILLERS`,
   each ~3s spoken. The interval was previously 4s when filler density
   was the only defense against hijacks; once dispatch suppression
   landed, the interval was widened to 10s for less-frantic pacing. If
   suppression in defense #1 ever has to be weakened, drop the interval
   back to 4s to restore the old filler-density defense.
3. **Pinned probe task** (`_ACTIVE_PROBE_TASKS`). The probe runs as an
   asyncio task. If the LLM hijacks and Cartesia cancels the generator,
   the spawned task would normally be cancelled too. Storing it in a
   module-level set pins it until completion — so participant detection
   and the force-redirect still fire even after a hijack.
4. **Force-redirect via Twilio REST API** (`_force_redirect_to_conference`).
   When a human joins the conference, the probe task does TWO things:
   (a) returns True so the happy-path `AgentTransferCall` yield fires;
   (b) schedules a delayed (4s) REST API call to move the customer's call
   into the conference. The delay lets the "I'm connecting you" speech
   play in the happy path. If the LLM hijacked, the speech yield is
   dropped — the REST call fires anyway and pulls the customer in.
   `completed_flag` gates the REST call: if `AgentTransferCall` already
   moved the call, the REST call no-ops.

**Case-by-case mapping** (numbering matches the rollout discussion):
- Case 1: Hijack + human joins → force-redirect recovers the call.
- Case 2: Hijack + no human → `escalation_pending` Linear ticket gives team trail.
- Case 3: Hangup mid-call → REST update fails harmlessly, ticket exists.
- Case 4/5: Hijack triggers other tool / concurrent escalations → in_progress flag de-dupes.
- Case 6: WebRTC hangs on human side → pickup-page watchdog flashes red banner.
- Case 7: Conference orphan → `<Dial timeLimit="600">` safety cap.
- Case 8: Hijack + `end_call_with_goodbye` → recovery farewell replaces LLM's chosen farewell.
- Case 9: LLM never escalates → pre-LLM pattern matcher bypasses LLM and runs escalation.

If you change anything in this area: think through ALL 9 cases. The system
is designed so that **a caller asking for a human cannot end the call
without the team being notified** (`log_escalation_started` is the floor).
Don't introduce a code path that breaks that guarantee.

## Queue (Cases 10-12) — two implementations live, selected by `QUEUE_VERSION`

When `QUEUE_ENABLED=true`, callers who escalate while reps are busy hold in a
queue. Two implementations exist in the codebase; `QUEUE_VERSION` env var
selects which one runs:

- **`QUEUE_VERSION=v2`** (default after the v2 rollout) — **Twilio Enqueue**.
  Cartesia speaks one announcement, hands the call out to Twilio via REST API
  `call.update`, and the customer holds in a Twilio queue with real hold music
  + position updates + a press-1-to-leave-message option. Reps dequeue by
  clicking a Slack "Take next caller" button → browser pickup → `<Dial><Queue>`
  atomically bridges to the head-of-queue caller. Bridge recording happens on
  `<Dial>`. Plan file: `~/.claude/plans/crystalline-sleeping-aho.md`.

- **`QUEUE_VERSION=v1`** (preserved as instant rollback) — **in-Cartesia
  silent hold**. The customer stays in the Cartesia session; `hold_queue.py`
  owns a Python `asyncio` FIFO + shared poller; `_wait_in_queue` yields
  position-update TTS every 45s + conversational check-ins every 3 min via the
  LLM. No hold music (Cartesia Line SDK exposes no audio-injection event).

Rollback is **one env var flip + `cartesia env set`** (no code redeploy):
set `QUEUE_VERSION=v1` if v2 misbehaves. Both code paths live in the same
binary.

### The three invariants (preserved in BOTH v1 and v2)

- **Case 10: Hangup during queue wait** — caller hangs up while holding.
  - v1: `main.py` CallEnded handler reads `phase == "queue_wait"`, logs
    `outcome="abandoned_in_queue"`, calls `hold_queue.dequeue(call_id)`.
  - v2: Twilio's `/queue-action` Function fires with `QueueResult=hangup` →
    posts the `abandoned_in_queue` Linear ticket via Linear API directly from
    the Function. Cartesia's CallEnded fires too but sees
    `phase == "queue_handoff"` and suppresses its own abandoned ticket
    (Twilio is authoritative once the call is handed off).

- **Case 11: Queue hard-timeout** — `MAX_QUEUE_WAIT_SECONDS` elapses.
  - v1: `_wait_in_queue`'s loop notices `elapsed >= max_wait`, speaks the
    `QUEUE_TIMEOUT_INTAKE_MESSAGE`, dequeues, hands the LLM control of
    callback intake → `outcome="callback_logged"`.
  - v2: `/queue-wait` Function returns `<Leave/>` after `MAX_QUEUE_WAIT_SECONDS`,
    queue-action redirects into `/queue-press` (the voicemail+callback intake
    flow), and the chained Functions post **ONE consolidated Slack DM** with
    both the voicemail audio link AND the caller's keypad-entered callback
    number. Linear `outcome="voicemail_logged"`.

- **Case 12: Dispatch race** — two reps / two callers / atomic FIFO.
  - v1: `_ACTIVE_PROBES` counter incremented/decremented under `_QUEUE_LOCK`
    in `hold_queue.py`. Shared poller per process — one source of truth.
  - v2: not a code concern. Twilio's `<Dial><Queue>` pops the head atomically;
    two reps clicking simultaneously each pop a different caller. No counter,
    no lock, no shared poller in Python.

### "Is a rep busy?" in v2 — there is no explicit check

v1 has `try_admit` to decide if a slot is free. v2 deletes that question:
every escalation enters the Twilio queue, every dispatch is rep-driven via
the Slack button. **Busy reps physically can't click the button because
they're on a call.** The rep's attention IS the busy signal — no
`MAX_CONCURRENT_REPS`, no `_ACTIVE_PROBES`, no slot tracking. Twilio's queue
serializes naturally.

The one edge case: a rep clicks "Take next caller" while still on a call.
Twilio bridges them to a second call in a second browser tab. They'd have to
hang up the new leg or finish the current call first. Mitigation (out of v2
scope): a `/queue-claim` Function pre-check that returns `{busy: true}` if
the rep already has an in-progress call. Add only if premature clicks become
a real problem in burn-in.

### Cartesia → Twilio ownership boundary (v2 only)

Once `escalation.py` issues the REST `call.update` to `/enqueue-customer`,
the call leg leaves Cartesia. From that moment:

- The CallEnded event WILL fire in Cartesia (the WebSocket closes), but the
  abandoned-ticket handler must NOT fire — Twilio's `/queue-action` Function
  is now the source of truth for the outcome. Main.py CallEnded handler
  checks `phase == "queue_handoff"` and skips its own ticket.
- Linear tickets after this point are posted by **Twilio Functions, not
  Python**. The Functions service needs `LINEAR_API_KEY` + `LINEAR_TEAM_ID` in
  its env (mirror prod values).
- The bridge recording (when a rep joins via `<Dial><Queue>`) is on `<Dial>`,
  not `<Queue>` — the queue side is hold music, not a recordable conference.
  Recording-callback URL: `?type=queue_bridged`.

If you touch any of this, think through the boundary: who fires the final
ticket? Cartesia or Twilio? Mistake = duplicate tickets per call.

When the master flag (`QUEUE_ENABLED`) is off, neither queue runs —
`escalate_to_human` falls straight to the legacy probe-then-callback flow.

## The atomic-tool rule (the #1 bug source)

Two tools speak directly to the caller: `escalate_to_human` and
`end_call_with_goodbye`. When the LLM calls either, it MUST NOT generate
any text in the same turn — the tool produces all the speech.

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

- **Greeting**: does NOT mention "AI", "bot", or "automated". The bot is
  **nameless** — the greeting introduces "Basic Capital" only, no first
  name. (Earlier versions used "Alex"; removed at Will's request.)
- **If the caller asks for a name** ("what's your name?", "who am I
  talking to?"): the bot says something like "I'm the customer support
  agent for Basic Capital" — neutral, no first name.
- **If the caller asks if it's a bot/AI** ("are you a bot?", "is this
  AI?"): the bot MUST be truthful. Identify specifically as
  **"AI customer support agent for Basic Capital"**. Then offer to keep
  helping OR transfer to a human. Only escalate if the caller actually
  picks the human path.

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

## Business hours and after-hours behavior

Business hours: **Mon–Fri, 9 AM – 5 PM Eastern**. Outside that window, the
FAQ agent still runs in full — callers can ask any FAQ question. The only
differences after-hours:

- Greeting swaps to `AFTER_HOURS_GREETING` ("we're outside business hours…
  I can still answer general questions or take a message").
- A short `AFTER_HOURS_PROMPT_NOTE` is appended to the system prompt so the
  LLM knows live transfers aren't possible and avoids "connecting you now"
  phrasing.
- `escalate_to_human` runs with `after_hours=True`: skips the probe wait
  entirely (no one's online to click the Slack button), sends an FYI-only
  Slack ping (no button), and goes straight to `AFTER_HOURS_UNAVAILABLE_MESSAGE`
  which leads into the standard callback intake flow.

Earlier the after-hours path was a strict one-shot voicemail agent. That
was replaced 2026-05-15 — callers wanted FAQs answered 24/7 and the
voicemail flow was discarded. Don't reintroduce a separate closed-hours
agent unless explicitly asked.

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

## Twilio Functions and Asset (post-handoff recording + browser pickup)

The `twilio_functions/` directory is the **source of truth**; Twilio's
hosted copies must match. Edit here, paste into Twilio Console, redeploy.

Functions in the Service `bc-voice-functions`:
- `/conference-join` — TwiML for the conference-join number. Enables
  recording, sets up the recording callback URL, and resolves the
  conference name. Caller-ID resolution: tries `event.From`, then
  `event.Caller`, then `event.OriginalFrom`, then `event.ForwardedFrom`,
  then falls back to a Twilio REST API `calls(CallSid).fetch()` lookup.
  This API path is necessary because Cartesia's `AgentTransferCall` was
  observed to leave `event.From` empty on this webhook even though the
  Call record itself carries the original caller. Accepts a `?conf=`
  query string override (used by the REST-API force-redirect path).
- `/recording-callback` — receives Twilio's `recordingStatusCallback` POST
  when the recording is ready, posts a Slack DM with caller number,
  duration, and listen link.
- `/agent-token` — issues Twilio Voice SDK Access Tokens (120s TTL) to
  the browser pickup page. Granted only `outgoingApplicationSid =
  TWIML_APP_SID` so the browser can only dial through our TwiML App.
- `/agent-dial` — TwiML the TwiML App invokes when the browser SDK calls
  `device.connect({ params: { conference } })`. Drops the responder's
  browser leg into the named conference.

Asset:
- `/agent-pickup.html` — static page the Slack button opens. Loads the
  Voice JS SDK from `https://unpkg.com/@twilio/voice-sdk@2.10.0/dist/twilio.min.js`
  (the official `sdk.twilio.com` URLs returned 403s during testing —
  don't switch back without verifying). Reads `?conf`, `?customer`,
  `?intent` from query string; displays customer + intent prominently;
  10s watchdog flips a red "call the customer directly at +1..." banner
  if the WebRTC handshake stalls.

All Functions + the Asset must be **Public visibility** in the Twilio
Console (top-right of the editor) — without this, Twilio's voice network
can't hit them and the browser can't load the page.

Service environment variables (NOT in project `.env` — Functions can't
read your `.env`):
- `SLACK_WEBHOOK_URL` — incoming webhook for `#ops-stale-tickets`. Used
  by `recording-callback.js`. The Python code reads the same URL from
  the project `.env`; keep both in sync when rotating.
- `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWIML_APP_SID` — used
  by `agent-token.js` to sign Access Tokens. `ACCOUNT_SID` is supplied
  automatically by the Functions runtime.

## Toll-free import flow (Cartesia ↔ Twilio integration)

Toll-free `+1 (888) 460-4901` is imported into Cartesia via their
preview "import Twilio number" API (3-step curl, documented in
[README.md](README.md)). Cartesia handles the Twilio voice-webhook
wiring internally — the toll-free's voice config in Twilio Console is
managed by Cartesia. Don't change it by hand or you'll break inbound.

Key resource IDs (stored only here for reference, not in code):
- Provider ID: `ata_T2rKFZkdtiWwyUhP7i8osb` (the Twilio↔Cartesia connection).
- Phone number ID: `ap_VtewXiUMyw4A84AdWBptP3` (the toll-free).
- Agent ID: `agent_CicivQhXS56dgUehm3B1Ea`.

The 218 area code number Cartesia provisioned originally is still
assigned to the agent as a parked backup. Either number routes inbound
calls to the bot. Release the 218 once toll-free has run a week of clean
traffic.

## Twilio API Key auth bug (workaround)

Standard API Keys created in this Twilio account (parent or any
subaccount) consistently fail Basic Auth with **error 20003
"Authenticate"**, even when created programmatically via the REST API
(no UI in the loop). Account-level Auth Tokens work fine. Reproducible
with both the parent and a freshly-created subaccount. This appears to
be a Twilio-side account flag or bug; needs a Twilio support ticket
to resolve.

**Workaround used by the Cartesia provider connect**: pass the
**Account SID** as `api_key_sid` and the **Auth Token** as
`api_key_secret`. Cartesia accepts this combo; Twilio recognizes
Account SID + Auth Token as a valid Basic Auth credential pair. The
provider already in production was created this way.

Implication: when rotating Twilio creds, you rotate the **Auth Token**
(coarser than rotating an API Key). To rotate, PATCH the Cartesia
provider with the new Auth Token value:

```bash
curl -X PATCH "https://api.cartesia.ai/agents/phone-numbers/providers/ata_T2rKFZkdtiWwyUhP7i8osb" \
  -H "Authorization: Bearer $CARTESIA_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Cartesia-Version: 2025-04-16" \
  -d '{"api_key_secret":"<new auth token>"}'
```

If Twilio resolves the API Key auth bug, swap back to a proper Standard
API Key (api_key_sid=SK..., api_key_secret=secret) for tighter scoping.

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
