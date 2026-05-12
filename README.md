# Basic Capital FAQ Voice Agent

Phone bot that answers general Basic Capital questions, transfers callers to a
human when one's available, falls back to a callback intake when the team is
tied up, and captures voicemails outside business hours. Every call ends with
a Linear ticket + Slack DM so ops has a written record — including for callers
who hang up mid-call and for after-hours voicemails.

## Stack

- **Cartesia Line** — agent framework (Sonic-3 TTS + Ink-Whisper STT + orchestration)
- **Twilio** — phone numbers + simul-ring hunt group + conference bridge + Functions
- **Anthropic Claude Haiku 4.5** — LLM (via LiteLLM, inside Cartesia Line)
- **Linear** — per-call ops tickets
- **Slack** — incoming-call pings, follow-up requests, post-call summaries, conference recording links

## Files

```
voice-bot1/
├── main.py                    # Entry point: get_agent, system prompt, business-hours gate, voicemail
├── faqs.md                    # FAQ knowledge base (loaded at container start)
├── escalation.py              # escalate_to_human (announce → Slack ping → probe + filler audio → outcome)
├── slack_ticket.py            # record_followup (callback / email logging)
├── linear_ticket.py           # log_call_complete (Linear ticket + Slack post-call summary)
├── twilio_functions/          # Twilio Functions source-of-truth (hosted on Twilio)
│   ├── conference-join.js     # TwiML for the conference-join number (records + sets up callback)
│   ├── recording-callback.js  # receives recording callback, posts Slack DM with listen link
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

- [Cartesia](https://cartesia.ai) — API key, `cartesia auth login`
- [Twilio](https://twilio.com) — paid account (Trial mode rejects inbound transfers via error 21264; **upgrade is required for production**). Two phone numbers (~$2/mo total):
  - Main number — customer-facing
  - Conference-join number — internal, transfer destination
- [Anthropic](https://console.anthropic.com) — API key (`sk-ant-…`)
- [Linear](https://linear.app/settings/api) — API key + team UUID
- [Slack](https://api.slack.com/apps) — incoming webhook URL

### 2. Twilio configuration

1. **Buy two numbers** in the Twilio console.
2. **Set up the Twilio Function Service** (replaces the older TwiML-Bin path):
   - Console → Develop → Functions and Assets → Services → Create Service.
   - Name: `bc-voice-functions`.
   - Add env var: `SLACK_WEBHOOK_URL` (same value as your `.env`).
   - Add two Functions:
     - `/conference-join` — paste `twilio_functions/conference-join.js`, set **Public** visibility.
     - `/recording-callback` — paste `twilio_functions/recording-callback.js`, set **Public** visibility.
   - Save each Function, then **Deploy All**. See [`twilio_functions/README.md`](twilio_functions/README.md) for detail.
3. **Conference-join number** → set its voice webhook to **Function: bc-voice-functions / `/conference-join`**, HTTP POST.
4. **Main number** → set its voice webhook to the URL Cartesia gives you when you attach the number in the Cartesia dashboard.

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

1. https://api.slack.com/apps → Create New App → From scratch
2. Enable "Incoming Webhooks" and add one to your `#bc-support` channel (or your own DM for testing)
3. Copy the webhook URL — same URL handles every message type the bot fires.

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

### Business hours (Mon–Fri, 9am–7pm ET by default)

1. **Greeting** — Alex introduces by name; mentions recording + that personal advice goes to a human; asks how to help.
2. **FAQ match** — answers from `faqs.md` in 1–2 sentences, then asks if the caller needs anything else.
3. **Escalation** (account-specific / advice / "I want a human" / off-FAQ) — `escalate_to_human` announces, Slack-pings the team, probes the hunt group while playing periodic filler audio every 10s so the caller doesn't sit in silence:
   - **Available** → log Linear ticket (`outcome=transferred`) + `AgentTransferCall` → conference-join Twilio Function records the post-handoff conversation, posts a Slack DM with the recording link when done.
   - **Unavailable** → Alex leads the caller straight into callback intake (name + phone), `record_followup` logs to Slack, `end_call_with_goodbye` wraps with `outcome=callback_logged`. If the caller proactively asks to email instead, that path is still available (`outcome=email_logged`), but the bot doesn't offer it.
4. **Off-topic** — one polite redirect; second off-topic question → `end_call_with_goodbye(outcome=other)`.
5. **Caller hangs up mid-call** — `CallEnded` wrapper logs `outcome=abandoned` so it still shows up in Linear/Slack.
6. **Asked "are you a bot?"** — Alex confirms truthfully ("AI customer support specialist for Basic Capital") and offers to keep helping or transfer. Only escalates if the caller actually picks the human path.

### Outside business hours

1. **Voicemail greeting** plays — mentions hours, recording disclosure, invites a quick message.
2. **Caller speaks their message** (one turn).
3. **LLM calls `end_voicemail`** → Linear ticket with `outcome=voicemail` and the message captured in the recap. Cartesia keeps the audio in its dashboard, linked from the Linear ticket.
4. **Safety net**: if the LLM doesn't wrap after the first turn and the caller speaks again, a wrapper force-ends with a generic farewell + `outcome=voicemail` ticket pointing ops to the Cartesia audio. This prevents the LLM from getting stuck in a "anything else?" loop.
5. **Silent caller** → no `UserTurnEnded` ever fires; eventually they hang up → `CallEnded` → `outcome=abandoned`.

## Testing checklist

Call your main BC Twilio number and verify:

- [ ] **FAQ question** ("What's the 401(k) contribution limit?") → answered from FAQ, asks "anything else?"
- [ ] **Withdrawal flow** → asks IRA-or-401(k) clarifier, gives rules first, then offers to connect for paperwork
- [ ] **Account-specific** ("what's my balance?") → escalates without trying to answer
- [ ] **Advice question** ("should I do a Roth conversion?") → escalates with compliance language
- [ ] **Hunt group answers** → caller bridged; Linear ticket (`outcome=transferred`) lands; ~30s after hangup, Slack `🎞 Conference recording ready` DM lands with caller number + duration + listen link
- [ ] **Hunt group times out** → bot speaks filler audio every ~10s during the wait; bot leads into callback intake ("what's your full name?"); Slack follow-up + Linear ticket (`outcome=callback_logged`) land
- [ ] **Caller hangs up mid-call** → Linear ticket with `outcome=abandoned`
- [ ] **Off-topic** ("what's the weather?") → polite redirect; second off-topic question ends the call cleanly
- [ ] **"Are you a bot?"** → Alex confirms truthfully (AI customer support specialist), offers to keep helping or transfer
- [ ] **Frustrated caller** ("REPRESENTATIVE REPRESENTATIVE") → bot says just "One moment." with NO apology or empathy preamble, then triggers probe
- [ ] **After hours** → voicemail greeting plays, caller leaves message, Linear ticket with `outcome=voicemail` lands, message in the recap
- [ ] **After hours, silent caller** → no message left, caller hangs up after a while → `outcome=abandoned` ticket

## Common issues

- **Twilio Trial account rejects inbound transfers** (error 21264) — upgrade to a paid account. Trial mode requires verified caller IDs for inbound transfers, which doesn't work for real customer numbers.
- **Linear auth fails** — header is `Authorization: lin_api_xxx`, NOT `Bearer lin_api_xxx`. The code already handles this.
- **Cartesia deep-links 404** — URL must be `?tab=calls&call=ac_sid_xxx` (query params, not path segments). Make sure `CARTESIA_AGENT_ID` is set.
- **Bot says digits weirdly** ("four hundred and one k") — pronunciation table in the system prompt has the spoken-vs-written forms; FAQ uses phonetic ("four-oh-one K") for spoken text. Tool params (Linear/Slack) should be the digit form ("401k").
- **Bot speaks twice on hangup/escalation** — atomic-tool rule violated. `escalate_to_human` and `end_call_with_goodbye` MUST be called with no LLM-generated text in the same turn; the tool handles all speech. Same rule applies to `end_voicemail`.
- **Conference recording Slack DM never arrives** — verify `SLACK_WEBHOOK_URL` is set in the Twilio Function Service environment variables (different place from your `.env`). Both Functions also need to be Public-visible and deployed.
- **Browser `cartesia chat` stuck on "Active 0:00:00"** — known WebRTC flakiness on some networks; phone path works fine. Switch to a hotspot or just test via the phone number.

## What's deliberately NOT in v1

- 45-second relevance gate (early-end on spam callers)
- Distress detection
- Spanish support
- Account-specific lookups (always escalate)
- Auto-toggle availability based on team status
- Per-call dynamic conference names (today everyone shares `bc-active` — flagged in `.env.example`)
- Cross-linking the post-handoff conference recording back to the Linear ticket (manual correlation by timestamp / caller for now)
- Anthropic prompt caching (currently every turn re-sends the full system prompt)
- Retirement Mortgage explanations (legacy product, sunsetted, routes to humans)
- Prior-year contributions, ACAT/in-kind rollovers, 401(k) loans, catch-up contributions — IRS allows these; BC doesn't operationally support them yet. FAQs say so explicitly.
