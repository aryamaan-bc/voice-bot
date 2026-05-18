/**
 * Twilio Function — waitUrl handler for the v2 hold queue.
 *
 * Called REPEATEDLY by Twilio while a caller is in the queue. Each
 * invocation returns a finite TwiML response (~30-60s of audio); when
 * playback finishes, Twilio re-invokes this URL with updated QueueTime.
 *
 * Every TwiML response except <Leave/> has the same shape:
 *   <Gather press-1>
 *     <Say>You're number N in line. Press 1 to leave a message...</Say>
 *     <Play>hold music</Play>
 *   </Gather>
 *
 * So the caller is reminded of their position AND the press-1 option on
 * every cycle (~every minute as music segments end and Twilio re-invokes
 * waitUrl). The press-1 keypad is live throughout — pressing 1 routes
 * to /queue-press immediately. At MAX_QUEUE_WAIT_SECONDS we return
 * <Leave/>; queue-action fires with QueueResult=leave and redirects
 * the caller to /queue-press (voicemail intake).
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

  // Propagate query params so /queue-press can post a complete Slack DM.
  const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
  const queuePressUrl =
    `/queue-press?${[qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&')}`;

  // <Gather> wraps everything so press-1 is always live. timeout=1 means
  // after the nested <Play>/<Say> finishes, Gather waits 1s of silence
  // before completing → Twilio re-invokes waitUrl. Each cycle is
  // roughly music_duration + 1s.
  const gather = twiml.gather({
    numDigits: 1,
    timeout: 1,
    action: queuePressUrl,
    method: 'POST',
  });

  // Speak position + press-1 reminder on EVERY invocation, including
  // the first (QueueTime=0). The caller benefits from knowing their
  // position immediately, and the press-1 reminder stays fresh
  // throughout the wait. The /enqueue-customer pre-queue announcement
  // already mentioned press-1, so the first Say here is a confirmation
  // that the option is still available + their actual queue position.
  gather.say(
    { voice: 'Polly.Joanna' },
    `You're number ${queuePosition} in line. Press 1 to leave a message, or stay on the line.`
  );
  gather.play(holdMusicUrl);

  console.log(
    `queue-wait call_id=${callId} pos=${queuePosition} elapsed=${queueTime}s`
  );
  callback(null, twiml);
};
