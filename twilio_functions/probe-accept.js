/**
 * Twilio Function — gather-action handler for the responder press-1 gate.
 *
 * Flow: the probe places an outbound call to a responder's cell with
 * inline TwiML that plays "Press 1 to accept" and routes <Gather> at
 * this Function. Twilio POSTs us the digit pressed.
 *
 * - Digits == "1": return TwiML joining the conference (name passed
 *   via `?conf=bc-XXXX` query param embedded by escalation.py).
 * - Anything else (wrong digit, gather timeout, voicemail can't press):
 *   return TwiML that hangs up cleanly. The conference room stays
 *   empty, the probe poll sees no participant, the customer falls
 *   through to the callback intake.
 *
 * Why this exists: when the responder's cell is on another call, the
 * carrier may roll our probe outbound to voicemail. Without this gate,
 * voicemail would technically "join" the conference (silent), the probe
 * would think the transfer succeeded, and the customer would be dropped
 * into a room with no human in it. Voicemails can't press digits, so
 * this gate filters them out cleanly.
 *
 * Source of truth: this repo. Deploy via Twilio Console → Functions →
 * bc-voice-functions → /probe-accept, visibility Public.
 */
exports.handler = function (context, event, callback) {
  const twiml = new Twilio.twiml.VoiceResponse();
  const digits = event.Digits || '';
  const confName = event.conf || 'bc-active';

  if (digits === '1') {
    twiml.dial().conference(
      {
        startConferenceOnEnter: true,
        endConferenceOnExit: true,
        beep: false,
      },
      confName
    );
  } else {
    // Wrong digit, no input (gather timeout), voicemail picked up, or
    // any other "not a confirming human" case — hang up without joining
    // the conference.
    twiml.hangup();
  }
  callback(null, twiml);
};
