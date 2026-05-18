# Basic Capital FAQ Voice Agent

Phone bot that answers general Basic Capital questions 24/7, transfers
callers to a human when one's available (business hours only), and falls
back to a callback intake when the team is tied up or offline. Every call
ends with a Linear ticket + Slack DM so ops has a written record —
including for callers who hang up mid-call and for after-hours callback
requests.

**Public line:** `+1 (888) 460-4901` (toll-free). Routes through Cartesia's
Twilio integration into the deployed agent.

## Stack

- **Cartesia Line** — agent framework (Sonic-3 TTS + Ink-Whisper STT + orchestration). Inbound numbers imported via Cartesia's Twilio integration.
- **Twilio** — toll-free PSTN number, conference bridge, Twilio Functions, Voice JS SDK for browser-based human pickup.
- **Anthropic Claude Haiku 4.5** — LLM (via LiteLLM, inside Cartesia Line).
- **Linear** — per-call ops tickets (including an immediate `escalation_pending` ticket fired the moment an escalation starts, separate from the final-outcome ticket).
- **Slack** — incoming-call pings with browser-pickup buttons, follow-up requests, post-call summaries, conference recording links. All currently routed to `#ops-stale-tickets`.

## Files

```
voice-bot1/
├── main.py                    # Entry point: get_agent, system prompt, business-hours gate (greeting/prompt swap only — FAQ runs 24/7), Case 9 pre-LLM bypass
├── faqs.md                    # FAQ knowledge base (loaded at container start)
├── escalation.py              # run_escalation_flow (announce → Slack button → probe → force-redirect / callback intake) + user_wants_human pattern matcher
├── slack_ticket.py            # record_followup (callback / email logging)
├── linear_ticket.py           # log_call_complete + log_escalation_started (immediate paper trail)
├── twilio_functions/          # Twilio Functions source-of-truth (hosted on Twilio)
│   ├── conference-join.js     # TwiML for the conference-join number (records, derives conf name from caller ID via Twilio API lookup)
│   ├── recording-callback.js  # receives recording callback, posts Slack DM with listen link
│   ├── agent-token.js         # issues Twilio Voice SDK Access Tokens to the browser pickup page
│   ├── agent-dial.js          # TwiML the TwiML App calls when the browser SDK dials; drops the human into the conference
│   ├── agent-pickup.html      # static page the Slack "Take call in browser" button opens; loads Voice JS SDK, displays caller/intent, watchdog for stuck WebRTC
│   ├── probe-accept.js        # (legacy) press-1 voicemail filter for the old phone-probe path; unused when BROWSER_PICKUP=true
│   └── README.md              # deploy + env-var setup for the Twilio Function Service
├── twilio_huntgroup.xml       # legacy reference TwiML (Functions superseded this)
├── cartesia.toml              # Agent ID (set by `cartesia init`)
├── pyproject.toml
├── .env.example               # Copy to .env, fill in
├── CLAUDE.md                  # Agent-facing project guide
└── README.md
```

## Setup (one time)

### 1. Accounts

- [Cartesia](https://cartesia.ai) — API key (CLI stores it at `~/.cartesia/config.toml`); `cartesia auth login`
- [Twilio](https://twilio.com) — paid account (Trial mode rejects inbound transfers via error 21264; **upgrade is required for production**). Two numbers:
  - **Public toll-free** — customer-facing, imported into Cartesia (see step 2).
  - **Conference-join number** — internal, transfer destination (~$1/mo).
- [Anthropic](https://console.anthropic.com) — API key (`sk-ant-…`)
- [Linear](https://linear.app/settings/api) — API key + team UUID
- [Slack](https://api.slack.com/apps) — incoming webhook URL

### 2. Twilio configuration

#### Phone numbers

1. **Buy a toll-free number** for the public line: Twilio Console → Phone Numbers → Buy → Country US, Type Toll-Free. Cost ~$2/mo.
2. **Buy/reuse a local number** for the conference-join leg (internal). Set `CONFERENCE_JOIN_NUMBER` in `.env`.
3. **Import the toll-free into Cartesia.** Cartesia handles the Twilio voice-webhook wiring for you; do NOT set the toll-free's voice webhook manually. Three curls in the project root:

   ```bash
   export CARTESIA_API_KEY=$(grep "api_key" ~/.cartesia/config.toml | cut -d"'" -f2)
   export TWILIO_ACCOUNT_SID="AC..."     # parent account SID
   export TWILIO_API_KEY_SID="$TWILIO_ACCOUNT_SID"      # see "API Key auth workaround" below
   export TWILIO_API_KEY_SECRET="..."    # parent account Auth Token

   # 1. Connect Twilio account to Cartesia → returns provider id
   curl -X POST "https://api.cartesia.ai/agents/phone-numbers/providers" \
     -H "Authorization: Bearer $CARTESIA_API_KEY" -H "Content-Type: application/json" \
     -H "Cartesia-Version: 2025-04-16" \
     -d "{\"type\":\"twilio\",\"account_sid\":\"$TWILIO_ACCOUNT_SID\",\"api_key_sid\":\"$TWILIO_API_KEY_SID\",\"api_key_secret\":\"$TWILIO_API_KEY_SECRET\",\"region\":\"us1\"}"

   # 2. Import the toll-free → returns phone number id
   curl -X POST "https://api.cartesia.ai/agents/phone-numbers" \
     -H "Authorization: Bearer $CARTESIA_API_KEY" -H "Content-Type: application/json" \
     -H "Cartesia-Version: 2025-04-16" \
     -d '{"label":"BC Production Line","number":"+1XXXXXXXXXX","provider":{"id":"ata_..."}}'

   # 3. Assign to your agent
   curl -X PATCH "https://api.cartesia.ai/agents/phone-numbers/ap_..." \
     -H "Authorization: Bearer $CARTESIA_API_KEY" -H "Content-Type: application/json" \
     -H "Cartesia-Version: 2025-04-16" \
     -d '{"agent_id":"agent_..."}'
   ```

> **Twilio API Key auth workaround:** on some accounts, Standard API Keys fail Basic Auth with error 20003. Workaround: pass the **Account SID** as `api_key_sid` and the **Auth Token** as `api_key_secret` in the Cartesia connect call. Cartesia accepts this; Twilio recognizes Account SID + Auth Token as valid Basic Auth. File a Twilio support ticket if you hit it (Twilio recognizes the bug; rotate creds via Auth Token reset until they fix it).

#### Twilio Functions

Set up the Twilio Function Service (`bc-voice-functions`):

1. Console → Develop → Functions and Assets → Services → Create Service. Name: `bc-voice-functions`.
2. Add env vars to the Service (NOT in your project `.env` — Functions can't read it):
   - `SLACK_WEBHOOK_URL` — incoming webhook for `#ops-stale-tickets`.
   - `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWIML_APP_SID` — for browser pickup (see `twilio_functions/README.md`).
3. Add Functions (paste contents from this repo, set **Public** visibility on each):
   - `/conference-join` ← `twilio_functions/conference-join.js`
   - `/recording-callback` ← `twilio_functions/recording-callback.js`
   - `/agent-token` ← `twilio_functions/agent-token.js`
   - `/agent-dial` ← `twilio_functions/agent-dial.js`
4. Add Asset (Upload File, Public visibility): `/agent-pickup.html` ← `twilio_functions/agent-pickup.html`.
5. Deploy All.

#### TwiML App for browser pickup

Create a TwiML App that routes browser-dialed calls into the conference:
1. Twilio Console → Voice → TwiML → TwiML Apps → Create. Name: `bc-browser-pickup`.
2. Voice Request URL: `https://<your-bc-voice-functions-domain>/agent-dial`. HTTP POST.
3. Save the TwiML App SID into the Service env vars as `TWIML_APP_SID`.

#### Conference-join number wiring

Set the conference-join number's voice webhook (in Twilio Console) to: **Function: bc-voice-functions / `/conference-join`**, HTTP POST. The toll-free public number is **handled by Cartesia** via the import flow above — don't set its webhook manually.

### 3. Linear configuration

1. Personal API key at https://linear.app/settings/api (starts with `lin_api_`)
2. Get the team UUID:
   ```bash
   curl -s -H "Authorization: $LINEAR_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"{teams{nodes{id name key}}}"}' \
        https://api.linear.app/graphql | python3 -m json.tool
   ```
   Use the `id` (UUID), not the `key`.
3. (Recommended) Create a dedicated project so call tickets don't pollute the main board.

### 4. Slack configuration

1. https://api.slack.com/apps → Create New App → From scratch (already done as "BC Voice Bot").
2. Enable "Incoming Webhooks" and add one to your `#ops-stale-tickets` channel (or your own DM for testing).
3. Copy the webhook URL — same URL handles every message type the bot fires. Set as `SLACK_WEBHOOK_URL` in both project `.env` AND the Twilio Service env vars (recording-callback.js reads it from the Service).

## Local development

```bash
pip install cartesia
cartesia auth login
pip install -e .

cp .env.example .env
# fill in real values; for browser-only testing set DEMO_MODE=true and
# only ANTHROPIC_API_KEY is strictly required (escalation skips Twilio).

cartesia chat 8000      # browser playground
```

## Deployment

```bash
cartesia init                                                # one time
cartesia deploy                                              # push code
cartesia env set --from=.env --agent-id=<your-agent-id>      # push env vars
cartesia logs --follow
```

**Important:** `cartesia deploy` pushes code; `cartesia env set` pushes env vars. They're separate. If you change `.env`, you must run both for production to see the new values.

## Call flow

### Business hours (Mon–Fri, 9am–5pm ET by default)

1. **Greeting** — bot introduces "Basic Capital" only (no first name); mentions recording + that personal advice goes to a human; asks how to help.
2. **FAQ match** — answers from `faqs.md` in 1–2 sentences, then asks if the caller needs anything else.
3. **Escalation** (account-specific / advice / "I want a human" / off-FAQ). Two ways it triggers:
   - **LLM-initiated**: the bot calls `escalate_to_human`.
   - **Pattern-initiated (Case 9 backstop)**: `main.py`'s pre-LLM wrapper detects an explicit human-request phrase in the user's transcript and runs the escalation flow itself — even if the LLM was about to ignore the request. The first-to-fire wins; the other is a no-op.

   Once running, the flow:
   - Speaks the announcement, fires the Slack ping with a "Take call in browser" button, immediately writes an `escalation_pending` Linear ticket so the team has a paper trail no matter what happens next, then polls the per-call Twilio conference for a human to join.
   - Plays filler audio every ~10 seconds during the wait. (LLM dispatch is suppressed for the duration of the escalation — see CLAUDE.md "LLM-hijack recovery" — so the filler cadence is about caller comfort rather than hijack resistance.)
   - **Available** (human joined): log Linear ticket (`outcome=transferred`), speak "I'm connecting you to my human supervisor now," yield `AgentTransferCall` → caller bridges into the conference. A parallel **force-redirect** also fires via Twilio REST API as a belt-and-suspenders mechanism — if the LLM hijacked the tool mid-wait and the `AgentTransferCall` yield got dropped, the REST redirect still moves the caller into the conference.
   - **Unavailable** (60s timeout, nobody clicked): the bot leads the caller straight into callback intake (name + phone), `record_followup` logs to Slack, `end_call_with_goodbye` wraps with `outcome=callback_logged`.
   - **LLM hijack + tries to end the call** (Case 8): `end_call_with_goodbye` detects active escalation, replaces the LLM's farewell with a recovery message ("Sorry about that — our team has the request and someone will follow up shortly"), still logs the ticket.
4. **Browser pickup** — the team picks up calls in a browser via WebRTC, not on their phones. The Slack "Take call in browser" button opens `agent-pickup.html`, which shows the caller's number + intent, has a Join button, and a 10s watchdog that flashes an urgent red banner ("call the customer directly at +1...") if the WebRTC handshake hangs.
5. **Off-topic** — one polite redirect; second off-topic question → `end_call_with_goodbye(outcome=other)`.
6. **Caller hangs up mid-call** — `CallEnded` wrapper logs `outcome=abandoned` so it still shows up in Linear/Slack.
7. **Asked "what's your name?"** — bot says it's "the customer support agent for Basic Capital" (no first name).
8. **Asked "are you a bot?"** — bot confirms truthfully ("AI customer support agent for Basic Capital") and offers to keep helping or transfer. Only escalates if the caller actually picks the human path. The pattern matcher deliberately does NOT match this question (it's a question, not a request — see CLAUDE.md).

### Queue (when reps are all busy)

When `QUEUE_ENABLED=true`, a caller who escalates while reps are busy lands in a hold queue. Two implementations live in the codebase and `QUEUE_VERSION` selects which runs:

- **`QUEUE_VERSION=v2`** (default) — **Twilio Enqueue with hold music.** Cartesia speaks one announcement, then hands the call out to a Twilio queue. The caller hears real hold music + a "you're number N in line" position update every minute. After 3 minutes the caller can press 1 to leave a voicemail + keypad callback number (consolidated into one Slack DM). At 15 minutes the queue auto-routes to the voicemail intake. Reps dequeue by clicking a Slack "Take next caller" button — Twilio bridges them to the head-of-queue caller via `<Dial><Queue>`. No explicit "rep busy" check; the rep's attention is the bottleneck.

- **`QUEUE_VERSION=v1`** (preserved as instant rollback) — **in-Cartesia silent hold.** Customer stays in the Cartesia session. Position updates speak every 45s, conversational check-ins ("still want to wait?") every 3 min. No hold music — Cartesia Line SDK exposes no audio-injection event. See CLAUDE.md "Queue (Cases 10-12)" for the failure-mode handling shared by both implementations.

Rollback procedure: edit `QUEUE_VERSION=v1` in `.env.staging.local`, run `cartesia env set`. No code redeploy — both paths are live in the same binary. See `STAGING.md` and `~/.claude/plans/crystalline-sleeping-aho.md` for the full v2 architecture, rollback runbook, and rollout slice list.

### Outside business hours

The FAQ agent answers everything the same way it does during business hours
— the only differences:

1. **Greeting** swaps to the after-hours version ("we're outside business hours… I can still answer general questions or take a message").
2. **System prompt** has a short note appended telling the LLM live transfers aren't possible and to use phrasing like "let me grab your details so someone can follow up on the next business day" when calling `escalate_to_human`.
3. **`escalate_to_human` short-circuits**: no probe wait, no filler audio, no "Take call in browser" Slack button (no one's online to click). The Slack ping fires as an FYI only, the `escalation_pending` Linear ticket lands as usual, then the bot goes straight to `AFTER_HOURS_UNAVAILABLE_MESSAGE` → standard callback intake (name + phone → `record_followup`) → `end_call_with_goodbye(outcome=callback_logged)`.
4. **Caller hangs up mid-call** → `outcome=abandoned` ticket, same as business hours.

## Testing checklist

Call `+1 (888) 460-4901` and verify:

- [ ] **FAQ question** ("What's the 401(k) contribution limit?") → answered from FAQ, asks "anything else?"
- [ ] **Withdrawal flow** → asks IRA-or-401(k) clarifier, gives rules first, then offers to connect for paperwork.
- [ ] **Account-specific** ("what's my balance?") → escalates without trying to answer.
- [ ] **Advice question** ("should I do a Roth conversion?") → escalates with compliance language.
- [ ] **Browser pickup** → after escalation, the Slack ping in `#ops-stale-tickets` shows a "Take call in browser" button. Click → pickup page shows caller number + intent → click Join → connects via WebRTC. ~30s after hangup, Slack `🎞 Conference recording ready` DM lands.
- [ ] **Nobody clicks within 60s** → bot leads into callback intake ("what's your full name?"); Slack follow-up + Linear ticket (`outcome=callback_logged`) land.
- [ ] **Case 1 hijack recovery** → call, ask for human, talk loudly during the wait ("hello hello hello"). The LLM may hijack mid-tool, but Taylor clicking the Slack button still pulls you into the conference via the force-redirect.
- [ ] **Case 8 — LLM tries to end the call mid-escalation** → ask for human, then say "OK forget it, bye". Should hear the recovery farewell ("Sorry about that — our team has the request..."), not the LLM's confused goodbye.
- [ ] **Case 9 — pre-LLM bypass** → say "uhh can you put me through to an actual human". Escalation announcement fires immediately, Slack ping lands, `escalation_pending` Linear ticket appears.
- [ ] **AI-identity regression** → ask "Are you a bot?" or "Are you a real person?" Should hear the AI disclosure offer to continue OR transfer, NOT the escalation announcement (the pattern matcher deliberately doesn't match question phrasing).
- [ ] **WebRTC failure on human side** → on the pickup page, deny mic permission then click Join. After 10s the red urgent banner should appear with the customer's phone number.
- [ ] **Caller hangs up mid-call** → Linear ticket with `outcome=abandoned`.
- [ ] **Off-topic** ("what's the weather?") → polite redirect; second off-topic question ends the call cleanly.
- [ ] **Frustrated caller** ("REPRESENTATIVE REPRESENTATIVE") → bot says just "One moment." with NO apology or empathy preamble, then triggers probe.
- [ ] **After hours, FAQ question** → after-hours greeting plays; bot answers the FAQ normally (e.g. 401(k) contribution limit) and asks "anything else?"
- [ ] **After hours, asks for human** → bot speaks an after-hours-appropriate announcement (no "connecting you now"), Slack FYI ping lands with no button, no probe runs, bot goes straight to "what's your full name?" callback intake. Linear ticket with `outcome=callback_logged` lands.
- [ ] **After hours, hangs up mid-call** → `outcome=abandoned` ticket, same as business hours.

## Common issues

- **Twilio Trial account rejects inbound transfers** (error 21264) — upgrade to a paid account. Trial mode requires verified caller IDs for inbound transfers, which doesn't work for real customer numbers.
- **Twilio Standard API Keys fail Basic Auth with error 20003** — known bug on this account. Workaround in the Cartesia provider connect: use `account_sid` as `api_key_sid` and Auth Token as `api_key_secret`. Both work as Basic Auth credentials with Twilio.
- **Linear auth fails** — header is `Authorization: lin_api_xxx`, NOT `Bearer lin_api_xxx`. The code already handles this.
- **Cartesia deep-links 404** — URL must be `?tab=calls&call=ac_sid_xxx` (query params, not path segments). Make sure `CARTESIA_AGENT_ID` is set.
- **Bot says digits weirdly** ("four hundred and one k") — pronunciation table in the system prompt has the spoken-vs-written forms; FAQ uses phonetic ("four-oh-one K") for spoken text. Tool params (Linear/Slack) should be the digit form ("401k").
- **Bot speaks twice on hangup/escalation** — atomic-tool rule violated. `escalate_to_human` and `end_call_with_goodbye` MUST be called with no LLM-generated text in the same turn; the tool handles all speech. There's also a code-level filter in `agent_with_abandoned_logging` that buffers AgentSendText events and drops them when an atomic tool call follows in the same turn.
- **Customer ends up in `bc-active` instead of the per-call conference** — `event.From` was empty on the conference-join webhook. `conference-join.js` falls back to a Twilio REST API `calls(sid).fetch()` lookup to recover the original `from` number. If this also returns empty, customer lands in the shared `bc-active` room and the human is alone in `bc-<caller_digits>`. Check Twilio Functions logs for the `conference-join API lookup` line.
- **Pickup page says "Twilio is not defined"** — the Voice JS SDK URL changed. We use `https://unpkg.com/@twilio/voice-sdk@2.10.0/dist/twilio.min.js`. The official `sdk.twilio.com` URLs appear to be deprecated.
- **Pickup page hangs on "Connecting to conference..."** — WebRTC blocked (mic permission denied, firewall, codec mismatch). After 10s the red urgent banner appears with the customer's phone number for manual callback. The `escalation_pending` Linear ticket is the team's paper trail either way.
- **Conference recording Slack DM never arrives** — verify `SLACK_WEBHOOK_URL` is set in the Twilio Function Service environment variables (different place from your `.env`). All Functions also need to be Public-visible and deployed.
- **Browser `cartesia chat` stuck on "Active 0:00:00"** — known WebRTC flakiness on some networks; phone path works fine. Switch to a hotspot or just test via the phone number.

## What's deliberately NOT in v1

- 45-second relevance gate (early-end on spam callers)
- Distress detection
- Spanish support
- Account-specific lookups (always escalate)
- Auto-toggle availability based on team status
- Conference-orphan detection (human joined-then-dropped before customer arrived) — `<Dial timeLimit="600">` is the safety cap; full fix needs `conference statusCallback` event handler
- Cross-linking the post-handoff conference recording back to the Linear ticket (manual correlation by timestamp / caller for now)
- Anthropic prompt caching (currently every turn re-sends the full system prompt)
- Phone-probe-mode safety net — the force-redirect / pre-LLM bypass / Case 8 recovery layers are only wired into the `BROWSER_PICKUP=true` path. Phone probe (`BROWSER_PICKUP=false`) still works but lacks the new recovery layers; dead code to be removed.
- Retirement Mortgage explanations (legacy product, sunsetted, routes to humans)
- Prior-year contributions, ACAT/in-kind rollovers, 401(k) loans, catch-up contributions — IRS allows these; BC doesn't operationally support them yet. FAQs say so explicitly.
