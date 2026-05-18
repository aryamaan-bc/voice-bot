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
  // Twilio-hosted hold music fallback. The old com.twilio.music.classical
  // S3 bucket was emptied (returns 404 NoSuchKey + application/xml,
  // which crashed <Play> with Twilio alerts 11200 + 12300). Switched to
  // demo.twilio.com/docs/classic.mp3 — same Twilio-hosted CDN file used
  // in Twilio's <Play> docs examples, returns audio/mpeg + HTTP 200.
  // Override via HOLD_MUSIC_URL env var to use a branded MP3 (recommended:
  // host as a Twilio Asset in this Functions service so the URL stays
  // inside Twilio's CDN — future v2.1 enhancement).
  const holdMusicUrl = (context.HOLD_MUSIC_URL || '').trim() ||
    'https://demo.twilio.com/docs/classic.mp3';

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

  // <Gather input="dtmf"> wraps the Say + Play so the keypad is live
  // throughout the wait. timeout=1 → after Play finishes Twilio waits
  // 1s for digits before re-invoking waitUrl. Each cycle is roughly
  // music_duration + 1s. Press-1 routes immediately to /queue-press
  // (voicemail intake).
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
  gather.play(holdMusicUrl);

  console.log(
    `queue-wait call_id=${callId} pos=${queuePosition} elapsed=${queueTime}s`
  );
  callback(null, twiml);
};
