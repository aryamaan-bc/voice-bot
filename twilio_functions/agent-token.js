/**
 * Twilio Function — issues short-lived Voice SDK Access Tokens for the
 * browser-based pickup page. Hit by `/agent-pickup.html` over CORS when
 * the responder clicks the Slack "Take call in browser" button.
 *
 * Query params:
 *   - identity (optional): string used as the participant identity in
 *     Twilio. Defaults to "bc-agent". Length-capped to 32 chars.
 *
 * Returns JSON: { token, identity }
 *   - token is a JWT with a VoiceGrant pointing at our TwiML App
 *     (TWIML_APP_SID). When the browser SDK calls `device.connect()`,
 *     Twilio invokes the TwiML App's voice URL — /agent-dial — with
 *     whatever params we pass.
 *   - TTL is 120s. The Slack button URL is short-lived too; if a
 *     responder waits more than 2 minutes to click, they get a fresh
 *     token on click (the page calls /agent-token at click time, not at
 *     page load).
 *
 * Service env vars needed:
 *   - ACCOUNT_SID            (set by Twilio automatically)
 *   - TWILIO_API_KEY_SID     (Console → API keys & tokens)
 *   - TWILIO_API_KEY_SECRET  (Console → API keys & tokens)
 *   - TWIML_APP_SID          (Console → Voice → TwiML Apps)
 *
 * Source of truth: this repo. Deploy via Twilio Console → Functions →
 * bc-voice-functions → /agent-token, visibility Public.
 */
exports.handler = function (context, event, callback) {
  const AccessToken = Twilio.jwt.AccessToken;
  const VoiceGrant = AccessToken.VoiceGrant;

  const rawIdentity = (event.identity || 'bc-agent').toString();
  const identity = rawIdentity.replace(/[^a-zA-Z0-9_.-]/g, '').slice(0, 32) || 'bc-agent';

  const missing = [];
  if (!context.TWILIO_API_KEY_SID) missing.push('TWILIO_API_KEY_SID');
  if (!context.TWILIO_API_KEY_SECRET) missing.push('TWILIO_API_KEY_SECRET');
  if (!context.TWIML_APP_SID) missing.push('TWIML_APP_SID');
  if (missing.length) {
    const resp = new Twilio.Response();
    resp.appendHeader('Content-Type', 'application/json');
    resp.appendHeader('Access-Control-Allow-Origin', '*');
    resp.setStatusCode(500);
    resp.setBody({ error: `Service env vars missing: ${missing.join(', ')}` });
    return callback(null, resp);
  }

  const token = new AccessToken(
    context.ACCOUNT_SID,
    context.TWILIO_API_KEY_SID,
    context.TWILIO_API_KEY_SECRET,
    { identity, ttl: 120 }
  );

  token.addGrant(
    new VoiceGrant({
      outgoingApplicationSid: context.TWIML_APP_SID,
      incomingAllow: false,
    })
  );

  const resp = new Twilio.Response();
  resp.appendHeader('Content-Type', 'application/json');
  resp.appendHeader('Access-Control-Allow-Origin', '*');
  resp.setBody({ token: token.toJwt(), identity });
  callback(null, resp);
};
