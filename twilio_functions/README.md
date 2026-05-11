# Twilio Functions

Serverless JS endpoints hosted by Twilio. The files here are the source
of truth; Twilio's hosted copies must match. Edit here, paste into the
Twilio Console, redeploy.

## Functions

| File | Path | Purpose |
|------|------|---------|
| `conference-join.js` | `/conference-join` | TwiML returned when 917-979-6392 receives an inbound call (i.e., when the bot transfers a customer in). Drops the caller into `bc-active`, enables recording, sets up the recording-ready callback. |
| `recording-callback.js` | `/recording-callback` | Twilio POSTs here when a conference recording finishes processing. Posts a Slack DM with the listen URL and duration. |

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

## Why Functions and not TwiML Bins

Tried Bins first; they were hard to navigate in the current Console UI,
and we needed dynamic behavior anyway (the Slack post in
`recording-callback.js`). Functions also let us keep both endpoints in
one Service with one env var set, sharing a deploy.
