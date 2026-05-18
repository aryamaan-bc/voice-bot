/**
 * Twilio Function — voicemail-intake entry point.
 *
 * Reached two ways:
 *   1. Caller presses 1 in /queue-wait during the press-1 stage.
 *   2. Hard-timeout: /queue-wait returns <Leave/>, queue-action fires
 *      with QueueResult=leave and redirects here.
 *
 * Returns TwiML that:
 *   1. Speaks a voicemail prompt.
 *   2. <Record>s the caller for up to 30 seconds.
 *   3. On completion (caller presses # or hangs up), chains to
 *      /queue-after-record, which gathers the callback number and then
 *      posts ONE consolidated Slack DM via /queue-callback-saved.
 *
 * Query params propagated from upstream: call_id, caller, intent.
 *
 * NOTE: do NOT speak more than ~5s before <Record> — the caller has
 * already spent 3+ minutes on hold and may be impatient.
 */
exports.handler = (context, event, callback) => {
  console.log('queue-press event:', JSON.stringify(event));

  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);

  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const afterRecordUrl =
    `/queue-after-record?${[qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&')}`;

  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.say(
    { voice: 'Polly.Joanna' },
    'Sure — leave a message after the tone. Press pound when done, or just hang up.'
  );
  twiml.record({
    maxLength: 30,
    finishOnKey: '#',
    playBeep: true,
    trim: 'trim-silence',
    transcribe: false,
    action: afterRecordUrl,
    method: 'POST',
  });
  // If <Record> times out without any speech (rare — the caller pressed
  // 1 then said nothing), fall through to queue-after-record anyway so
  // the team still gets a Slack DM (with a "no audio" placeholder).
  twiml.redirect({ method: 'POST' }, afterRecordUrl);

  callback(null, twiml);
};
