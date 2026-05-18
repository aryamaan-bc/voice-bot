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
  const mode = (event.mode || '').toString();

  // v2 queue path — rep dequeues by joining the named Twilio queue.
  // `<Dial><Queue>` atomically pops the head-of-queue caller and bridges
  // them to the rep's browser leg. FIFO + atomicity provided by Twilio
  // (no Python lock needed). Recording goes on `<Dial>`, not `<Queue>`,
  // because the queue side is hold music — only the bridged conversation
  // is recordable.
  if (mode === 'queue') {
    const queueName = context.TWILIO_QUEUE_NAME || 'bc-support';
    const recordingCallbackUrl =
      `https://${context.DOMAIN_NAME}/recording-callback?type=queue_bridged`;
    twiml
      .dial({
        record: 'record-from-answer',
        recordingStatusCallback: recordingCallbackUrl,
        recordingStatusCallbackEvent: 'completed',
      })
      .queue(queueName);
    return callback(null, twiml);
  }

  // v1 / legacy conference path — rep joins a specific named conference
  // that the customer's call leg is already in.
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
