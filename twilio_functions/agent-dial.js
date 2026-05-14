/**
 * Twilio Function — TwiML returned by the TwiML App (TWIML_APP_SID) when
 * the browser Voice SDK calls `device.connect({ params: { conference }})`.
 * Drops the responder's browser leg into the named conference room,
 * joining the customer who's already waiting there.
 *
 * Params (POSTed by Twilio from the SDK's `connect()` call):
 *   - conference: conference name to join (e.g., "bc-19175551234"). Must
 *     match the per-call name escalation.py / conference-join.js compute
 *     from the caller's phone number.
 *
 * Recording is intentionally NOT configured here. conference-join.js
 * (the customer's leg) already sets `record: 'record-from-start'` on
 * the conference, which is conference-scoped — adding it again on the
 * responder's leg would do nothing useful and risks a config mismatch.
 *
 * endConferenceOnExit is false on the responder leg: if the responder's
 * browser drops (flaky wifi, accidental tab close), the customer isn't
 * abruptly disconnected. The customer's leg already has
 * endConferenceOnExit:true, so the conference ends correctly when the
 * customer hangs up.
 *
 * Source of truth: this repo. Deploy via Twilio Console → Functions →
 * bc-voice-functions → /agent-dial, visibility Public.
 *
 * IMPORTANT: After deploying, set the TwiML App (Console → Voice →
 * TwiML Apps → bc-browser-pickup) Voice Request URL to:
 *   https://<your-functions-domain>/agent-dial   (HTTP POST)
 */
exports.handler = function (context, event, callback) {
  const twiml = new Twilio.twiml.VoiceResponse();
  const rawConf = (event.conference || '').toString();
  const confName = rawConf.replace(/[^a-zA-Z0-9-]/g, '').slice(0, 64);

  if (!confName) {
    twiml.say(
      { voice: 'Polly.Joanna' },
      'No conference specified. Hanging up.'
    );
    twiml.hangup();
    return callback(null, twiml);
  }

  twiml.dial().conference(
    {
      startConferenceOnEnter: true,
      endConferenceOnExit: false,
      beep: false,
    },
    confName
  );

  callback(null, twiml);
};
