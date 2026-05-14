# Twilio Functions

Serverless JS endpoints hosted by Twilio. The files here are the source
of truth; Twilio's hosted copies must match. Edit here, paste into the
Twilio Console, redeploy.

## Functions

| File | Path | Purpose |
|------|------|---------|
| `conference-join.js` | `/conference-join` | TwiML returned when the conference-join Twilio number receives an inbound call (i.e., when the bot transfers a customer in). Drops the caller into the per-call conference room, enables recording, sets up the recording-ready callback. |
| `recording-callback.js` | `/recording-callback` | Twilio POSTs here when a conference recording finishes processing. Posts a Slack DM with the listen URL, caller number, and duration. |
| `probe-accept.js` | `/probe-accept` | Gather-action handler for the responder press-1 gate. When the probe rings a responder's cell, they hear "Press 1 to accept" — if they press 1, this Function joins them into the conference. If voicemail picks up (can't press digits) or they press anything else, it hangs up cleanly. Prevents the voicemail-false-positive bug. Unused when `BROWSER_PICKUP=true`. |
| `agent-token.js` | `/agent-token` | Browser-pickup mode only. Issues a short-lived (120s) Twilio Voice SDK Access Token to the `/agent-pickup.html` page so the responder can join the conference via WebRTC. |
| `agent-dial.js` | `/agent-dial` | Browser-pickup mode only. TwiML hit by the TwiML App when the browser SDK's `device.connect()` fires. Drops the responder's browser leg into the named conference. |
| `agent-pickup.html` | `/agent-pickup.html` | Browser-pickup mode only. Static Asset (not a Function). The page the Slack "Take call in browser" button opens. Loads the Voice JS SDK, fetches a token, joins the conference on click. |

## Service

All functions live in a single Twilio Service: **`bc-voice-functions`**.
`agent-pickup.html` is uploaded to the same Service as a public Asset.

## Environment variables

Set in Twilio Console → Functions → bc-voice-functions → Environment
Variables. (Separate from the project `.env` — Twilio Functions can't
read your `.env`.)

Always required:
- `SLACK_WEBHOOK_URL` — the incoming webhook for `#bc-support` (same
  one used by the rest of the codebase).

Required only for browser pickup (`BROWSER_PICKUP=true`):
- `TWILIO_API_KEY_SID` — Console → Account → API keys & tokens → Create.
- `TWILIO_API_KEY_SECRET` — shown ONCE at API Key creation. Save it.
- `TWIML_APP_SID` — Console → Voice → TwiML → TwiML Apps → Create.
  Friendly name `bc-browser-pickup`. Voice Request URL set to
  `https://<your-functions-domain>/agent-dial`, HTTP POST.

`ACCOUNT_SID` is provided automatically by the Functions runtime; no
need to set it manually.

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

### First-time deploy of the browser-pickup files

When adding `agent-token.js`, `agent-dial.js`, and `agent-pickup.html`
for the first time:

1. **Service env vars first.** In the Service settings, add
   `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, and `TWIML_APP_SID`
   (see Environment variables above).
2. **Create the TwiML App** (Console → Voice → TwiML → TwiML Apps →
   Create). Friendly name `bc-browser-pickup`. Leave the Voice Request
   URL blank for now; you'll set it after the Functions are deployed.
3. **Add the two new Functions** (`agent-token`, `agent-dial`) in the
   bc-voice-functions Service. Paste contents, set visibility to
   **Public**, Deploy All.
4. **Add the HTML asset.** In the Service editor, Add → Upload File,
   choose `agent-pickup.html`. Set its path to `/agent-pickup.html` and
   visibility to **Public**. Deploy All.
5. **Wire up the TwiML App.** Go back to the TwiML App you created in
   step 2 and set Voice Request URL to
   `https://<your-functions-domain>/agent-dial`, HTTP POST. Save.
6. **Smoke test.** Open
   `https://<your-functions-domain>/agent-pickup.html?conf=bc-test` in
   a browser, click "Join call." You should connect to an empty
   conference named `bc-test` (you can hear a hold message). Close the
   tab to disconnect.
7. **Flip the flag.** In the project `.env`, set `BROWSER_PICKUP=true`
   and `TWILIO_FUNCTIONS_DOMAIN=<your-functions-domain>`. Then
   `cartesia env set --from=.env --agent-id=<id>` and
   `cartesia deploy`.

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
