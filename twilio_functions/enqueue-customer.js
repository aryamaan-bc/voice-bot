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

  // Pre-queue announcement. Tells the caller they're being held + sets
  // hold-music expectation. Polly voice — Twilio doesn't have
  // Cartesia's voice, so the change from Cartesia → Polly is the audio
  // cue that the call has crossed from Cartesia to Twilio.
  //
  // No press-1 mention here: Twilio's <Enqueue> waitUrl TwiML only
  // permits <Say>, <Play>, <Pause>, <Redirect>, <Leave> — <Gather> is
  // forbidden, so DTMF capture inside the queue isn't possible. The
  // only escape from the queue (other than a rep dequeuing) is the
  // 15-min hard-timeout <Leave/> in /queue-wait, which routes to the
  // voicemail-intake flow automatically.
  twiml.say(
    { voice: 'Polly.Joanna' },
    "Putting you on hold for our team. Please stay on the line — we'll connect you as soon as a rep frees up."
  );

  // `waitUrlMethod` (NOT `waitMethod` — Twilio's TwiML schema rejected
  // the latter as an unknown attribute, which is what triggered the
  // first round of application errors).
  twiml.enqueue(
    { waitUrl, waitUrlMethod: 'POST', action, method: 'POST' },
    queueName
  );

  console.log(`enqueue-customer queueName=${queueName} callId=${callId} caller=${caller}`);
  callback(null, twiml);
};
