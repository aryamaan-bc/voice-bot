/**
 * Twilio Function — entry point for the v2 Twilio-Enqueue queue.
 *
 * Called by Cartesia's `escalation.py` via REST API `call.update`. The
 * customer's call leg is redirected here AFTER Cartesia speaks the
 * "putting you through to our team" announcement. From this point on
 * Twilio owns the call until the rep dequeues OR the caller hangs up.
 *
 * Expected query params:
 *   call_id     — Cartesia call ID (for cross-system correlation)
 *   caller      — E.164 caller phone (for Slack/Linear ticket recap)
 *   intent      — short summary of why the caller wants a human
 *
 * Returns TwiML <Enqueue> with:
 *   - waitUrl  → /queue-wait (re-invoked while caller holds)
 *   - action   → /queue-action (fires on queue exit, any reason)
 *
 * Source of truth: this repo. To deploy: upload via Serverless API or
 * paste into Twilio Console → Functions → bc-voice-functions-staging.
 */
exports.handler = (context, event, callback) => {
  console.log('enqueue-customer event:', JSON.stringify(event));

  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || event.From || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);
  const queueName = context.TWILIO_QUEUE_NAME || 'bc-support';

  // Build the wait/action URLs. Cartesia's REST update will hit this
  // function over POST, then Twilio re-invokes waitUrl repeatedly (every
  // ~30-60s as each TwiML response finishes) while the caller holds.
  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const baseParams = [qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&');
  const waitUrl = `https://${context.DOMAIN_NAME}/queue-wait?${baseParams}`;
  const action  = `https://${context.DOMAIN_NAME}/queue-action?${baseParams}`;

  const twiml = new Twilio.twiml.VoiceResponse();

  // Pre-queue announcement: tells the caller they're being held, sets
  // expectations for hold music, AND surfaces the press-1 callback
  // option from the very first second of the queue. Without this the
  // caller didn't know press-1 was an option until ~3 min into the
  // wait (when /queue-wait's press-1 prompt previously kicked in).
  // Polly voice — Twilio doesn't have Cartesia's voice, so the change
  // from Cartesia → Polly is the audio cue that the call has crossed
  // from Cartesia to Twilio.
  twiml.say(
    { voice: 'Polly.Joanna' },
    'Putting you on hold for our team. You can press 1 anytime to leave a callback message, or stay on the line.'
  );

  twiml.enqueue(
    { waitUrl, waitMethod: 'POST', action, method: 'POST' },
    queueName
  );

  console.log(`enqueue-customer queueName=${queueName} callId=${callId} caller=${caller}`);
  callback(null, twiml);
};
