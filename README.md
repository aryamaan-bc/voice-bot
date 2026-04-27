# Basic Capital FAQ Voice Agent

A voice bot that answers FAQs about Basic Capital, transfers to a human when
one's available, and creates a Linear callback ticket when nobody picks up.

## Stack

- **Cartesia Line** — agent framework (Sonic-3 TTS + Ink-Whisper STT + orchestration)
- **Twilio** — phone numbers + simul-ring hunt group
- **Anthropic Claude Haiku** — LLM
- **Linear** — callback tickets
- **Slack** — incoming-call pings to the support team

## Files

```
bc-faq-agent/
├── main.py                # Agent entry point
├── faqs.md                # FAQ knowledge base ← FILL THIS IN
├── tools/
│   ├── escalation.py      # escalate_to_human (simul-ring + slack ping)
│   └── linear.py          # create_callback_ticket
├── twilio_huntgroup.xml   # TwiML for the hunt group (paste into TwiML Bin)
├── pyproject.toml
├── .env.example           # Copy to .env, fill in
└── README.md
```

## Setup (one time)

### 1. Accounts you need

- [Cartesia](https://cartesia.ai) — get API key
- [Twilio](https://twilio.com) — buy two phone numbers (~$2/mo total):
  - Main number (callers dial this)
  - Hunt group number (internal, never published)
- [Anthropic](https://console.anthropic.com) — API key
- [Linear](https://linear.app/settings/api) — API key + team ID
- [Slack](https://api.slack.com/apps) — incoming webhook URL

### 2. Twilio configuration

1. **Buy two numbers** in the Twilio console.
2. **Create a TwiML Bin** (Console → Runtime → TwiML Bins → Create new):
   - Paste the contents of `twilio_huntgroup.xml`
   - Replace the placeholder phone numbers with Taylor + Aryamaan's cells
   - Replace `callerId` with your main BC Twilio number
   - Save, copy the TwiML Bin URL
3. **Configure the hunt-group number's voice webhook** to point at the TwiML Bin URL.
4. **Configure the main number's voice webhook** to point at Cartesia (the
   Cartesia dashboard provides this URL when you attach the number).

### 3. Linear configuration

1. Create a personal API key at https://linear.app/settings/api.
2. Get your team's UUID:
   ```bash
   curl -H "Authorization: $LINEAR_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"query":"{teams{nodes{id name}}}"}' \
        https://api.linear.app/graphql
   ```
3. (Optional but recommended) Create a dedicated Linear project for callbacks
   so they don't pollute your main board.

### 4. Slack configuration

1. Go to https://api.slack.com/apps → Create New App → From scratch
2. Enable "Incoming Webhooks"
3. Add a webhook to your `#bc-support` channel (create the channel first)
4. Copy the webhook URL

## Local development

```bash
# Install Cartesia CLI
pip install cartesia

# Authenticate
cartesia auth login

# Install deps
pip install -e .

# Set up environment
cp .env.example .env
# ... edit .env with real values

# Fill in faqs.md with the 19 FAQ Q/A pairs (copy from basiccapital.com/faq)

# Test locally
cartesia chat 8000
```

## Deployment

```bash
# Link this directory to a Cartesia agent (one time)
cartesia init

# Deploy
cartesia deploy

# Watch logs
cartesia logs --follow
```

## Testing checklist

After deploying, call your main BC Twilio number from your cell. Test each
branch:

- [ ] **FAQ question** ("Are there annual contribution limits?") → bot answers
      from the FAQ
- [ ] **Off-FAQ question** with hunt group answering → bot says it's getting a
      human, your test phone rings, you pick up, you're on the call with the
      original caller
- [ ] **Off-FAQ question** with hunt group not answering → bot waits 30s, then
      asks for callback number, creates a Linear ticket, reads back the ticket
      ID
- [ ] **Slack ping** fires in `#bc-support` whenever escalation happens
- [ ] **Linear ticket** appears with intent summary as title and full
      transcript in the description
- [ ] **Caller hangup** mid-ticket-creation → ticket still gets created (check
      Linear)
- [ ] **Advice question** ("Should I roll over my 401k?") → bot deflects to
      human, doesn't try to answer

## Common issues

- **Linear API auth fails**: header is `Authorization: lin_api_xxx`, NOT
  `Bearer lin_api_xxx`.
- **Phone numbers must be E.164**: `+14155551234`, not `(415) 555-1234`.
- **TwiML Bin not firing**: check the hunt-group number's voice webhook is set
  to the TwiML Bin URL, not a static file.
- **Bot won't escalate**: tighten the system prompt — Haiku can be over-eager
  to answer. Add explicit "if you're not 100% sure, escalate" language.
- **Slack ping doesn't fire**: webhook URL is correct? Channel exists? App is
  installed to the workspace?

## What's deliberately NOT in v1

- 45-second relevance gate (gracefully end calls from spammers)
- Distress detection
- Spanish support
- Account-specific lookups
- Auto-toggle availability
- Duplicate-ticket detection

These are all in the memo's Evolution section. Add when you have data showing
they're needed.
