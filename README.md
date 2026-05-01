# Basic Capital FAQ Voice Agent

Phone bot that answers general Basic Capital questions, transfers callers to a
human when one's available, and falls back to a callback or email follow-up
when the team is tied up. Every call ends with a Linear ticket + Slack DM so
ops has a written record (including for callers who hang up mid-call).

## Stack

- **Cartesia Line** — agent framework (Sonic-3 TTS + Ink-Whisper STT + orchestration)
- **Twilio** — phone number + simul-ring hunt group + conference bridge
- **Anthropic Claude Haiku 4.5** — LLM (via LiteLLM)
- **Linear** — per-call ops tickets
- **Slack** — incoming-call pings, follow-up requests, post-call summaries

## Files

```
voice-bot1/
├── main.py                # Entry point: get_agent, system prompt, business-hours gate
├── faqs.md                # FAQ knowledge base (loaded at container start)
├── escalation.py          # escalate_to_human (announce → Slack ping → probe → outcome)
├── slack_ticket.py        # record_followup (callback / email logging)
├── linear_ticket.py       # log_call_complete (Linear ticket + Slack post-call summary)
├── twilio_huntgroup.xml   # TwiML for the conference-join number
├── cartesia.toml          # Agent ID (set by `cartesia init`)
├── pyproject.toml
├── .env.example           # Copy to .env, fill in
└── README.md
```

## Setup (one time)

### 1. Accounts

- [Cartesia](https://cartesia.ai) — API key, `cartesia auth login`
- [Twilio](https://twilio.com) — two phone numbers (~$2/mo total):
  - Main number (callers dial this)
  - Conference-join number (internal — Cartesia transfers the caller here)
- [Anthropic](https://console.anthropic.com) — API key (`sk-ant-…`)
- [Linear](https://linear.app/settings/api) — API key + team UUID
- [Slack](https://api.slack.com/apps) — incoming webhook URL

### 2. Twilio configuration

1. **Buy two numbers** in the Twilio console.
2. **Create a TwiML Bin** (Console → Runtime → TwiML Bins → Create new):
   - Paste the contents of `twilio_huntgroup.xml`
   - Replace placeholder cells with Taylor's + Aryamaan's numbers
   - Save and copy the TwiML Bin URL
3. **Conference-join number's voice webhook** → that TwiML Bin URL
4. **Main number's voice webhook** → the URL Cartesia gives you when you
   attach the number in the Cartesia dashboard

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
3. (Recommended) Create a dedicated project so call tickets don't pollute the
   main board.

### 4. Slack configuration

1. https://api.slack.com/apps → Create New App → From scratch
2. Enable "Incoming Webhooks" and add one to your `#bc-support` channel (or
   your own DM for testing)
3. Copy the webhook URL — same URL handles all three message types
   (live-call ping, follow-up logging, post-call summary)

## Local development

```bash
pip install cartesia
cartesia auth login
pip install -e .

cp .env.example .env
# fill in real values; for browser-only testing set DEMO_MODE=true and
# only ANTHROPIC_API_KEY is strictly required

cartesia chat 8000      # browser playground
```

## Deployment

```bash
cartesia init           # one time — links this dir to a Cartesia agent ID
cartesia deploy
cartesia logs --follow
```

## Call flow

1. **Greeting** — Alex introduces by name; mentions recording + that personal
   advice goes to a human; asks how to help.
2. **FAQ match** — answers from `faqs.md` in 1–2 sentences, then asks if the
   caller needs anything else.
3. **Escalation** (account-specific / advice / "I want a human" / off-FAQ) —
   `escalate_to_human` announces, Slack-pings the team, probes the hunt group:
   - **Available** → Linear ticket (`outcome=transferred`) + `AgentTransferCall`
   - **Unavailable** → Alex offers callback or email; `record_followup` logs
     it; `end_call_with_goodbye` wraps with `outcome=callback_logged` /
     `email_logged`
4. **Off-topic** — one polite redirect; second off-topic question →
   `end_call_with_goodbye(outcome=other)`
5. **Caller hangs up mid-call** — `CallEnded` wrapper logs
   `outcome=abandoned` so it still shows up in Linear/Slack
6. **Outside business hours** — closed-hours agent plays a recorded message,
   logs `outcome=closed_hours`, hangs up (no LLM, no team ring)

## Testing checklist

Call your main BC Twilio number and verify:

- [ ] **FAQ question** ("What's the 401(k) contribution limit?") → answered
      from FAQ, asks "anything else?"
- [ ] **Withdrawal flow** → asks IRA-or-401(k) clarifier, gives rules first,
      then offers to connect for paperwork
- [ ] **Account-specific** ("what's my balance?") → escalates without trying
      to answer
- [ ] **Advice question** ("should I do a Roth conversion?") → escalates with
      compliance language
- [ ] **Hunt group answers** → caller bridged, Linear ticket
      (`outcome=transferred`) appears
- [ ] **Hunt group times out** → callback or email path; Slack follow-up
      message + Linear ticket land
- [ ] **Caller hangs up mid-call** → Linear ticket with `outcome=abandoned`
      (and the Cartesia deep-link plays back the audio)
- [ ] **Off-topic** ("what's the weather?") → polite redirect; second
      off-topic question ends the call cleanly
- [ ] **"Are you a bot?"** → Alex confirms truthfully (AI customer support
      specialist), offers to keep helping or transfer
- [ ] **After hours** → closed-hours message, no LLM tokens spent

## Common issues

- **Linear auth fails** — header is `Authorization: lin_api_xxx`, NOT
  `Bearer lin_api_xxx`. The code already handles this.
- **Cartesia deep-links 404** — URL must be `?tab=calls&call=ac_sid_xxx`
  (query params, not path segments). Make sure `CARTESIA_AGENT_ID` is set.
- **Bot says digits weirdly** ("four hundred and one k") — pronunciation
  table in the system prompt has the spoken-vs-written forms; FAQ uses
  phonetic ("four-oh-one K") for spoken text. Tool params (Linear/Slack)
  should be the digit form ("401k").
- **Bot speaks twice on hangup/escalation** — atomic-tool rule violated.
  `escalate_to_human` and `end_call_with_goodbye` MUST be called with no
  LLM-generated text in the same turn; the tool handles all speech.
- **Browser `cartesia chat` stuck on "Active 0:00:00"** — known WebRTC
  flakiness; phone path works fine. Just use the phone number for testing.
- **Slack ping doesn't fire** — webhook URL set? Channel exists? App
  installed to the workspace?

## What's deliberately NOT in v1

- 45-second relevance gate (early-end on spam callers)
- Distress detection
- Spanish support
- Account-specific lookups (always escalate)
- Auto-toggle availability based on team status
- Duplicate-ticket detection (per-call ticketing means duplicates ARE the
  expected behavior — one ticket per call)
- Retirement Mortgage explanations (legacy product, sunsetted, routes to
  humans)
- Prior-year contributions, ACAT/in-kind rollovers, 401(k) loans, catch-up
  contributions — IRS allows these; BC doesn't operationally support them
  yet. FAQs say so explicitly.
