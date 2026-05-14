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
exports.handler = async function (context, event, callback) {
  // Debug: log all webhook params so we can see what Twilio sends when
  // AgentTransferCall redirects the customer to this Function. Visible
  // in Twilio Console → Functions → Logs.
  console.log('conference-join event:', JSON.stringify(event));

  // Caller ID resolution. Try webhook params first (From / Caller /
  // OriginalFrom / ForwardedFrom), then fall back to a Twilio API
  // lookup keyed by CallSid. The API path is needed because Cartesia's
  // AgentTransferCall redirects the customer's call in a way that
  // leaves event.From empty on this webhook, even though the Call
  // record itself still carries the original caller's number. The
  // explicit `?conf=` query param wins over everything for the future
  // case where the bot redirects via REST API with the conf in the URL.
  let customerNumber =
    event.From || event.Caller || event.OriginalFrom || event.ForwardedFrom || '';

  if (!customerNumber && event.CallSid) {
    try {
      const client = context.getTwilioClient();
      const call = await client.calls(event.CallSid).fetch();
      customerNumber = call.from || '';
      console.log(`conference-join API lookup: CallSid=${event.CallSid} from=${customerNumber}`);
    } catch (e) {
      console.warn('conference-join API lookup failed:', e && e.message);
    }
  }

  const explicitConf = (event.conf || '').toString().replace(/[^a-zA-Z0-9-]/g, '').slice(0, 64);
  const digits = customerNumber.replace(/\D/g, '');
  const confName = explicitConf || (digits ? `bc-${digits}` : 'bc-active');
  console.log(`conference-join routing to conf=${confName}`);

  const callbackUrl =
    `https://${context.DOMAIN_NAME}/recording-callback` +
    `?customer=${encodeURIComponent(customerNumber)}`;

  // Safety cap. The customer's call leg auto-ends after 10 minutes
  // regardless. Handles the orphan case where a human joined and then
  // dropped (browser crash, wifi blip) leaving the customer alone in
  // an active-but-empty conference with no waitUrl playing. Without
  // this, the customer would sit in silence until they hang up.
  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.dial({ timeLimit: 600 }).conference(
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
