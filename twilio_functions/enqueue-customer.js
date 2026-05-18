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
exports.handler = async (context, event, callback) => {
  console.log('enqueue-customer event:', JSON.stringify(event));

  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || event.From || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);
  const queueName = context.TWILIO_QUEUE_NAME || 'bc-support';

  // Stash {CallSid -> intent} in a Sync Map so the rep dashboard can show
  // why each caller is on hold (closes the "scroll Slack to remember"
  // gap). The map is read once per /dashboard-state poll. TTL=86400
  // (24h) keeps the map small without manual cleanup; by then the
  // CallSid is long gone from queue/conference lists anyway.
  //
  // Sync write is best-effort — if it fails (service quota, transient
  // 5xx) the queue flow still succeeds; dashboard just falls back to
  // showing the phone number alone for that caller. Non-blocking-ish:
  // we await it before callback so the function holds long enough for
  // the promise to resolve, but the customer's call isn't redirected
  // until callback fires either way (Twilio's voice network is the
  // one processing the TwiML).
  if (event.CallSid && intent) {
    try {
      const client = context.getTwilioClient();
      const mapName = 'bc-call-intent';
      const item = {
        key: event.CallSid,
        data: { intent, caller, callId, enteredAt: new Date().toISOString() },
        itemTtl: 86400,
      };
      try {
        await client.sync.v1.services('default').syncMaps(mapName)
          .syncMapItems.create(item);
      } catch (e) {
        // Map doesn't exist yet — create it then retry.
        if (e.status === 404) {
          await client.sync.v1.services('default').syncMaps.create({
            uniqueName: mapName,
          });
          await client.sync.v1.services('default').syncMaps(mapName)
            .syncMapItems.create(item);
        } else if (e.code === 54208) {
          // Item already exists (retry of same CallSid). Update instead.
          await client.sync.v1.services('default').syncMaps(mapName)
            .syncMapItems(event.CallSid)
            .update({ data: item.data, itemTtl: 86400 });
        } else {
          throw e;
        }
      }
    } catch (e) {
      console.warn(
        `enqueue-customer Sync intent write failed (non-fatal): ${e.message}`
      );
    }
  }

  // Build the wait/action URLs. Cartesia's REST update will hit this
  // function over POST, then Twilio re-invokes waitUrl repeatedly (every
  // ~30-60s as each TwiML response finishes) while the caller holds.
  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const baseParams = [qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&');
  const waitUrl = `https://${context.DOMAIN_NAME}/queue-wait?${baseParams}`;
  const action  = `https://${context.DOMAIN_NAME}/queue-action?${baseParams}`;

  const twiml = new Twilio.twiml.VoiceResponse();

  // Pre-queue announcement. First mention of "our team" (Cartesia's
  // V2_TRANSFER_ANNOUNCEMENT was deliberately kept brief), introduces
  // the press-1 callback option, and offers the stay-on-line
  // alternative. Polly voice — the Cartesia → Polly switch is the
  // audio cue that the call has crossed from Cartesia to Twilio.
  //
  // /queue-wait's <Gather input="dtmf"> keeps the keypad live for the
  // duration of the wait, so press-1 actually works at any point.
  // We only mention it HERE (not again in /queue-wait's per-cycle Say)
  // so the position updates stay short and non-redundant.
  twiml.say(
    { voice: 'Polly.Joanna' },
    "You're on hold for our team. Press 1 anytime to leave a callback message, or stay on the line."
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
