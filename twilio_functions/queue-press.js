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
    "Leave your message after the tone, then press pound when you're done or just hang up."
  );
  twiml.record({
    maxLength: 30,
    timeout: 10,           // 10s of silence before stopping (was 5s default —
                            // caller would get cut off mid-thought).
    finishOnKey: '#',
    playBeep: true,
    trim: 'trim-silence',
    transcribe: false,
    action: afterRecordUrl,
    method: 'POST',
  });
  // <Record>'s action URL always fires when recording ends (hangup, #,
  // maxLength, or silence-timeout). No need for a fallback Redirect —
  // Twilio reliably invokes the action.

  callback(null, twiml);
};
