/**
 * Twilio Function — invoked by /queue-wait's <Gather> when the caller
 * presses 1. Returns just <Leave/> so the caller exits the queue.
 * Twilio then fires the <Enqueue> action URL (/queue-action) with
 * QueueResult=leave, which redirects into /queue-press (voicemail).
 *
 * Why a separate Function: <Gather action="/queue-press"> would route
 * directly to the voicemail flow BUT the caller stays in the queue
 * the whole time, so the moment /queue-press's TwiML finishes Twilio
 * re-invokes /queue-wait and the caller hears another position update.
 * <Leave/> is the only way to actually exit the queue context.
 */
exports.handler = (context, event, callback) => {
  console.log('queue-leave event:', JSON.stringify(event));
  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.leave();
  callback(null, twiml);
};
