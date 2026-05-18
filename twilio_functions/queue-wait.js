/**
 * Twilio Function — waitUrl handler for the v2 hold queue.
 *
 * Called REPEATEDLY by Twilio while a caller is in the queue. Each
 * invocation returns a finite TwiML response (~30-60s of audio); when
 * playback finishes, Twilio re-invokes this URL with updated QueueTime.
 *
 * Three stages by elapsed QueueTime (per the plan):
 *   - 0-60s    : pure music. No announcement (caller just arrived).
 *   - 60-180s  : music + position update on each invocation.
 *   - 180-MAX  : music + position update + Press-1 prompt (Gather).
 *   - >= MAX   : <Leave/>. Queue-action fires with QueueResult=leave,
 *                routing into the voicemail-intake flow at /queue-press.
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

  if (queueTime >= 180) {
    // Press-1 stage. <Gather> wraps the audio so a single keypress
    // routes immediately to /queue-press. Timeout 30s = play music for
    // 30s before re-invoking waitUrl.
    const gather = twiml.gather({
      numDigits: 1,
      timeout: 30,
      action: queuePressUrl,
      method: 'POST',
    });
    gather.say(
      { voice: 'Polly.Joanna' },
      `You're number ${queuePosition} in line. Press 1 to leave a message, or stay on the line.`
    );
    gather.play(holdMusicUrl);
  } else if (queueTime >= 60) {
    // Position-update stage. Speak position, then music.
    twiml.say(
      { voice: 'Polly.Joanna' },
      `You're number ${queuePosition} in line — thanks for holding.`
    );
    twiml.play(holdMusicUrl);
  } else {
    // Pure-music stage. Caller just arrived; let them settle.
    twiml.play(holdMusicUrl);
  }

  console.log(
    `queue-wait stage=${queueTime >= 180 ? 'press1' : queueTime >= 60 ? 'position' : 'music'} ` +
    `call_id=${callId} pos=${queuePosition} elapsed=${queueTime}s`
  );
  callback(null, twiml);
};
