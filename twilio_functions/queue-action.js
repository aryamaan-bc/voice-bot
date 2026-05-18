/**
 * Twilio Function — action handler for the v2 hold queue.
 *
 * Fires whenever the caller exits the queue, for ANY reason. Twilio
 * passes a QueueResult parameter to distinguish what happened:
 *
 *   bridged    — a rep dequeued via <Dial><Queue> and the call bridged.
 *                Bridge happens BEFORE this handler runs; the bridged
 *                conversation continues regardless of what we return.
 *                Action: log Linear `transferred`. Return <Hangup/> as
 *                a no-op (bridged leg is already its own thing).
 *
 *   hangup     — caller hung up while queued (no rep ever clicked).
 *                Action: log Linear `abandoned_in_queue`.
 *
 *   leave      — <Leave/> verb fired (hard-timeout in /queue-wait, or
 *                press-1 redirecting in some Twilio configurations).
 *                Action: redirect to /queue-press to start the
 *                voicemail-intake chain.
 *
 *   redirected — REST API redirected the queue member out (unused for
 *                us today). Treat as a no-op log.
 *
 * Query params propagated from /enqueue-customer: call_id, caller, intent.
 *
 * Linear ticket posting: this Function makes the GraphQL call directly
 * (Node 18+ fetch) instead of going back through Cartesia. Same mutation
 * shape as linear_ticket.py for consistency. Requires LINEAR_API_KEY +
 * LINEAR_TEAM_ID in the Functions service env.
 */

const LINEAR_GRAPHQL_URL = 'https://api.linear.app/graphql';
const CREATE_ISSUE_MUTATION = `
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { identifier url }
  }
}
`;

const OUTCOME_LABELS = {
  transferred: 'Transferred to human',
  abandoned_in_queue: 'Caller hung up while queued',
};

async function logLinearTicket(context, { outcome, caller, intent, recap, callId }) {
  const apiKey = context.LINEAR_API_KEY;
  const teamId = context.LINEAR_TEAM_ID;
  if (!apiKey || !teamId) {
    console.warn('queue-action: LINEAR_API_KEY or LINEAR_TEAM_ID missing — skipping ticket');
    return;
  }
  const title = `${OUTCOME_LABELS[outcome] || outcome}: ${caller || '(unknown)'}`;
  const description =
    `**Caller:** ${caller || '(unknown)'}\n\n` +
    `**Intent:** ${intent || '(none captured)'}\n\n` +
    `**Outcome:** ${OUTCOME_LABELS[outcome] || outcome}\n\n` +
    `**Recap:** ${recap}\n\n` +
    `**Cartesia call ID:** ${callId || '(none)'}\n\n` +
    `_Logged by Twilio Function (queue-action.js)._`;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 10000);
    const resp = await fetch(LINEAR_GRAPHQL_URL, {
      method: 'POST',
      headers: {
        // Linear's auth header is the raw API key, no Bearer prefix.
        Authorization: apiKey,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query: CREATE_ISSUE_MUTATION,
        variables: { input: { teamId, title, description } },
      }),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!resp.ok) {
      console.error(`queue-action Linear post HTTP ${resp.status}`);
      return;
    }
    console.log(`queue-action Linear ticket created outcome=${outcome}`);
  } catch (err) {
    console.error('queue-action Linear post failed:', err.message);
  }
}

exports.handler = async (context, event, callback) => {
  console.log('queue-action event:', JSON.stringify(event));

  const queueResult = (event.QueueResult || '').toString();
  const queueTime = parseInt(event.QueueTime || '0', 10);
  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);

  const twiml = new Twilio.twiml.VoiceResponse();

  if (queueResult === 'bridged') {
    await logLinearTicket(context, {
      outcome: 'transferred',
      caller,
      intent,
      recap: `Caller bridged to rep via <Dial><Queue> after ${queueTime}s in queue.`,
      callId,
    });
    // Bridged leg already runs in its own <Dial> context; our TwiML
    // response here is a no-op. Hangup just closes this handler turn.
    twiml.hangup();
    return callback(null, twiml);
  }

  if (queueResult === 'hangup') {
    await logLinearTicket(context, {
      outcome: 'abandoned_in_queue',
      caller,
      intent,
      recap: `Caller disconnected while holding (${queueTime}s in queue). No rep ever joined.`,
      callId,
    });
    twiml.hangup();
    return callback(null, twiml);
  }

  if (queueResult === 'leave') {
    // <Leave/> fired (hard-timeout from /queue-wait, or press-1 in some
    // configurations). Route into the voicemail-intake chain.
    const qs = (k, v) => `${k}=${encodeURIComponent(v)}`;
    const params = [qs('call_id', callId), qs('caller', caller), qs('intent', intent)].join('&');
    twiml.redirect({ method: 'POST' }, `/queue-press?${params}`);
    return callback(null, twiml);
  }

  // Unknown / redirected — be defensive. Log to console, hang up cleanly.
  console.warn(`queue-action unhandled QueueResult=${queueResult}`);
  twiml.hangup();
  callback(null, twiml);
};
