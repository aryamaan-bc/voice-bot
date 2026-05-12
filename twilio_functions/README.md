# Twilio Functions

Serverless JS endpoints hosted by Twilio. The files here are the source
of truth; Twilio's hosted copies must match. Edit here, paste into the
Twilio Console, redeploy.

## Functions

| File | Path | Purpose |
|------|------|---------|
| `conference-join.js` | `/conference-join` | TwiML returned when the conference-join Twilio number receives an inbound call (i.e., when the bot transfers a customer in). Drops the caller into the per-call conference room, enables recording, sets up the recording-ready callback. |
| `recording-callback.js` | `/recording-callback` | Twilio POSTs here when a conference recording finishes processing. Posts a Slack DM with the listen URL, caller number, and duration. |
| `probe-accept.js` | `/probe-accept` | Gather-action handler for the responder press-1 gate. When the probe rings a responder's cell, they hear "Press 1 to accept" — if they press 1, this Function joins them into the conference. If voicemail picks up (can't press digits) or they press anything else, it hangs up cleanly. Prevents the voicemail-false-positive bug. |

## Service

Both functions live in a single Twilio Service: **`bc-voice-functions`**.

## Environment variables

The Service needs one env var:

- `SLACK_WEBHOOK_URL` — the incoming webhook for `#bc-support` (same one
  used by the rest of the codebase). Set in Twilio Console → Functions →
  bc-voice-functions → Environment Variables.

## Deploy workflow

1. Edit the `.js` files here.
2. Paste the new contents into the matching Function in Twilio Console
   (Functions → bc-voice-functions → click the Function name).
3. Set Function visibility to **Public** (right side of editor — required
   so Twilio's voice network and recording callbacks can hit it without
   auth).
4. Click **Deploy All** at the bottom of the Service editor.
5. URLs stay stable across deploys — no need to repoint the 917 webhook
   after each edit.

## 917 number wiring

The 917-979-6392 number's voice webhook should point at:

```
https://bc-voice-functions-XXXX.twil.io/conference-join
```

(Replace `XXXX` with the auto-generated suffix from your Service.) HTTP
method: POST.

The `/recording-callback` URL is wired up programmatically from inside
`conference-join.js` — no manual config needed there.

## Project env var

The Python code in `escalation.py` needs to know your Function
Service's domain so it can build the `/probe-accept` URL embedded in
probe outbound TwiML. After deploying the Functions:

1. Open the Service in Twilio Console.
2. Copy the domain (looks like `bc-voice-functions-1234.twil.io` — no
   protocol, no path).
3. Set `TWILIO_FUNCTIONS_DOMAIN=bc-voice-functions-1234.twil.io` in
   your project `.env`.
4. Push to the deployed Cartesia agent:
   `cartesia env set --from=.env --agent-id=<your-agent-id>`.

If the env var is unset, the probe falls back to direct conference
join (no press-1 gate). Safe to roll back this way if anything breaks.

## Why Functions and not TwiML Bins

Tried Bins first; they were hard to navigate in the current Console UI,
and we needed dynamic behavior anyway (the Slack post in
`recording-callback.js`). Functions also let us keep both endpoints in
one Service with one env var set, sharing a deploy.
