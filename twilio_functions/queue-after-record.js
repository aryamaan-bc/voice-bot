/**
 * Twilio Function — second step of the voicemail-intake chain.
 *
 * Receives the result of /queue-press's <Record> verb (RecordingUrl,
 * RecordingSid, RecordingDuration) and prompts the caller to enter a
 * callback number on the keypad. The next handler (queue-callback-saved)
 * posts ONE consolidated Slack DM with both the recording link and the
 * callback number.
 *
 * Why chain instead of inline: a single TwiML response can't have both
 * <Record> and <Gather> with their results forwarded to the same final
 * handler — Twilio's request lifecycle fires the action URL of each
 * separately. So we chain: /queue-press → /queue-after-record →
 * /queue-callback-saved.
 *
 * Query params propagated upstream: call_id, caller, intent.
 * POST params from <Record>: RecordingUrl, RecordingSid, RecordingDuration.
 *
 * Gather-timeout edge: if the caller hangs up without entering a number,
 * the <Redirect> fallback fires with an empty Digits so the Slack DM
 * still lands (with "callback number not provided" instead of digits).
 */
exports.handler = (context, event, callback) => {
  console.log('queue-after-record event:', JSON.stringify(event));

  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);

  // Recording info — may be empty if the caller never recorded anything
  // (e.g. they pressed 1 then said nothing for 30s).
  const recordingUrl = (event.RecordingUrl || '').toString();
  const recordingSid = (event.RecordingSid || '').toString();
  const recordingDuration = parseInt(event.RecordingDuration || '0', 10);

  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const savedParams = [
    qs('call_id', callId),
    qs('caller', caller),
    qs('intent', intent),
    qs('recordingUrl', recordingUrl),
    qs('recordingSid', recordingSid),
    qs('recordingDuration', recordingDuration.toString()),
  ].join('&');
  const savedUrl = `/queue-callback-saved?${savedParams}`;

  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.say(
    { voice: 'Polly.Joanna' },
    'Got it. Now please enter your callback number using the keypad, starting with area code, then press pound.'
  );
  const gather = twiml.gather({
    numDigits: 15,
    finishOnKey: '#',
    timeout: 10,
    action: savedUrl,
    method: 'POST',
  });
  gather.say({ voice: 'Polly.Joanna' }, 'Enter your callback number, then pound.');

  // Fallback if Gather times out without input — still hit the final
  // handler so the voicemail audio gets logged + Slack-posted.
  twiml.redirect({ method: 'POST' }, `${savedUrl}&Digits=`);

  callback(null, twiml);
};
