/**
 * Twilio Function — waitUrl handler for the v2 hold queue.
 *
 * Called REPEATEDLY by Twilio while a caller is in the queue. Each
 * invocation returns a finite TwiML response (~30-60s of audio); when
 * playback finishes, Twilio re-invokes this URL with updated QueueTime.
 *
 * Twilio's <Enqueue> waitUrl TwiML schema is restrictive: ONLY <Say>,
 * <Play>, <Pause>, <Redirect>, <Leave> are valid. <Gather> is NOT
 * allowed — Twilio throws "application error" if you try.
 *
 * So every TwiML response (except <Leave/> at hard-timeout) is just:
 *   <Say>You're number N in line — thanks for holding.</Say>
 *   <Play>hold music</Play>
 *
 * Position update fires every cycle (~every minute as music segments
 * end and Twilio re-invokes waitUrl). No press-1 inside the queue —
 * the keypad can't be live in waitUrl context. The only escape from
 * the queue (other than a rep dequeuing) is the 15-min hard-timeout
 * <Leave/>, which routes to /queue-press (voicemail intake).
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
  // Twilio's free classical hold music. Override via HOLD_MUSIC_URL env
  // var to use a branded MP3 (recommended: host as an Asset in the same
  // Functions service so the URL stays inside Twilio's CDN).
  const holdMusicUrl = (context.HOLD_MUSIC_URL || '').trim() ||
    'http://com.twilio.music.classical.s3.amazonaws.com/BusyStrings.wav';

  const twiml = new Twilio.twiml.VoiceResponse();

  if (queueTime >= maxWait) {
    // Hard timeout safety floor. <Leave/> kicks the caller out of the
    // queue; queue-action fires with QueueResult=leave; that handler
    // redirects to /queue-press to start the voicemail flow.
    console.log(`queue-wait HARD_TIMEOUT call_id=${callId} elapsed=${queueTime}s`);
    twiml.leave();
    return callback(null, twiml);
  }

  // Just <Say> + <Play> — both valid waitUrl verbs per Twilio's TwiML
  // schema. No <Gather> (would cause an application error).
  twiml.say(
    { voice: 'Polly.Joanna' },
    `You're number ${queuePosition} in line — thanks for holding.`
  );
  twiml.play(holdMusicUrl);

  console.log(
    `queue-wait call_id=${callId} pos=${queuePosition} elapsed=${queueTime}s`
  );
  callback(null, twiml);
};
