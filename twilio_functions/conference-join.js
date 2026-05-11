/**
 * Twilio Function — runs when 917-979-6392 receives an inbound call
 * (i.e., when the bot transfers a customer via AgentTransferCall).
 *
 * Returns TwiML that drops the caller into the shared `bc-active`
 * conference room. Conference is recorded from the start; when the
 * recording is ready, Twilio POSTs the metadata to /recording-callback,
 * which forwards a Slack DM with the listen URL.
 *
 * Source of truth: this repo. Twilio's hosted copy must match.
 * To deploy: paste into Twilio Console → Functions → bc-voice-functions
 * → /conference-join, set visibility to Public, click Deploy All.
 */
exports.handler = function (context, event, callback) {
  // DOMAIN_NAME is the auto-generated host for this Service
  // (e.g. bc-voice-functions-1234.twil.io). Used to build an absolute
  // URL for our own recording-callback endpoint — relative paths don't
  // work as Twilio webhooks.
  const callbackUrl = `https://${context.DOMAIN_NAME}/recording-callback`;

  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.dial().conference(
    {
      record: 'record-from-start',
      recordingStatusCallback: callbackUrl,
      recordingStatusCallbackEvent: 'completed',
      trim: 'trim-silence',
      beep: false,
      startConferenceOnEnter: true,
      endConferenceOnExit: true,
    },
    'bc-active'
  );
  callback(null, twiml);
};
