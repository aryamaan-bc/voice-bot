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
  //
  // event.From is the customer's phone number on the inbound transfer
  // (the bot redirects them here via AgentTransferCall). We forward it
  // through the recording callback so the post-call Slack DM can show
  // who was on the call without ops having to dig in Twilio Console.
  const customerNumber = event.From || '';

  // Conference name is derived from the caller's phone number so two
  // simultaneous callers from different numbers don't share a room.
  // Must stay in lockstep with escalation.py's `_derive_conf_name` —
  // same input, same output, no shared state. Fallback "bc-active" is
  // for the rare case where event.From is blank/anonymized.
  const digits = customerNumber.replace(/\D/g, '');
  const confName = digits ? `bc-${digits}` : 'bc-active';

  const callbackUrl =
    `https://${context.DOMAIN_NAME}/recording-callback` +
    `?customer=${encodeURIComponent(customerNumber)}`;

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
    confName
  );
  callback(null, twiml);
};
