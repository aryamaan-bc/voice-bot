/**
 * Twilio Function — waitUrl handler for the v2 hold queue.
 *
 * Called REPEATEDLY by Twilio while a caller is in the queue. Each
 * invocation returns a finite TwiML response (~30-60s of audio); when
 * playback finishes, Twilio re-invokes this URL with updated QueueTime.
 *
 * Twilio's <Enqueue> waitUrl TwiML schema permits: <Say>, <Play>,
 * <Pause>, <Hangup>, <Redirect>, <Leave>, and <Gather> with
 * input="dtmf" or input="speech". (Per Twilio docs: an earlier read
 * mistakenly excluded <Gather> — it IS allowed.)
 *
 * Every TwiML response (except <Leave/> at hard-timeout) is:
 *   <Gather input="dtmf" timeout=1 action="/queue-press">
 *     <Say>You're number N in line — thanks for holding.</Say>
 *     <Play>hold music</Play>
 *   </Gather>
 *
 * Position update fires every cycle (~every minute as music segments
 * end and Twilio re-invokes waitUrl). Press-1 keypad is live
 * throughout — pressing 1 routes to /queue-press (voicemail intake).
 * At MAX_QUEUE_WAIT_SECONDS we return <Leave/>; queue-action fires
 * with QueueResult=leave and redirects the caller to /queue-press.
 *
 * Twilio passes these inputs:
 *   QueueTime         — seconds since the caller was enqueued
 *   QueuePosition     — 1-indexed position (1 = head)
 *   CurrentQueueSize  — total callers in queue
 *
 * Plus our query params propagated from /enqueue-customer:
 *   call_id, caller, intent
 *
 * Press-1 routes to /queue-press (the voicemail-intake chain).
 */
exports.handler = (context, event, callback) => {
  console.log('queue-wait event:', JSON.stringify(event));

  const queueTime = parseInt(event.QueueTime || '0', 10);
  const queuePosition = parseInt(event.QueuePosition || '1', 10);
  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);

  const maxWait = parseInt(context.MAX_QUEUE_WAIT_SECONDS || '900', 10);
  // Position-update cadence — how often the caller hears "you're
  // number N in line" while holding. Twilio re-invokes waitUrl after
  // the nested verbs finish, so we control cadence by what we put
  // inside <Gather>. We use <Pause length=N> instead of <Play>music
  // because Twilio's free hosted music files are 3-5 min long, which
  // would make each cycle 3-5 min (caller gets no updates for most
  // of the wait). Pause is silent but gives predictable ~60s cycles.
  // (Tracking: upload a short branded MP3 as a Twilio Asset to
  // restore music with proper cadence — v2.3.)
  const positionUpdateInterval = parseInt(
    context.POSITION_UPDATE_INTERVAL_SECONDS || '60', 10
  );

  const twiml = new Twilio.twiml.VoiceResponse();

  if (queueTime >= maxWait) {
    // Hard timeout safety floor. <Leave/> kicks the caller out of the
    // queue; queue-action fires with QueueResult=leave; that handler
    // redirects to /queue-press to start the voicemail flow.
    console.log(`queue-wait HARD_TIMEOUT call_id=${callId} elapsed=${queueTime}s`);
    twiml.leave();
    return callback(null, twiml);
  }

  // Propagate query params so /queue-press has the full Slack-DM
  // context once the caller routes there.
  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const queuePressUrl =
    `/queue-press?${[qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&')}`;

  // <Gather input="dtmf"> wraps the Say + Pause so the keypad is live
  // throughout the wait. timeout=1 → after the nested verbs finish,
  // Gather waits 1s for digits before completing → Twilio re-invokes
  // waitUrl. Each cycle is Say (~3s) + Pause (positionUpdateInterval,
  // default 60s) + 1s = ~64s. Position update fires every cycle.
  // Press-1 during the Pause routes immediately to /queue-press.
  const gather = twiml.gather({
    input: 'dtmf',
    numDigits: 1,
    timeout: 1,
    action: queuePressUrl,
    method: 'POST',
  });
  gather.say(
    { voice: 'Polly.Joanna' },
    `You're number ${queuePosition} in line — thanks for holding.`
  );
  gather.pause({ length: positionUpdateInterval });

  console.log(
    `queue-wait call_id=${callId} pos=${queuePosition} elapsed=${queueTime}s`
  );
  callback(null, twiml);
};
