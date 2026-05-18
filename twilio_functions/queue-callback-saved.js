/**
 * Twilio Function — final step of the voicemail-intake chain.
 *
 * Posts ONE consolidated Slack DM and ONE Linear ticket containing
 * BOTH the voicemail audio link AND the caller's keypad-entered
 * callback number. The user specifically asked for these to be in a
 * single message they can act on, not two separate notifications.
 *
 * Query params propagated through the chain:
 *   call_id, caller, intent, recordingUrl, recordingSid, recordingDuration
 *
 * POST param from the preceding <Gather>:
 *   Digits — the keypad-entered callback number (may be empty if Gather
 *            timed out before the caller entered anything).
 *
 * Side effects:
 *   - POST to SLACK_WEBHOOK_URL — one DM with audio link + callback.
 *   - POST to Linear GraphQL — log outcome=voicemail_logged.
 *
 * TwiML returned: a "thanks, we'll be in touch" closer and <Hangup/>.
 *
 * Uses Node 18+ fetch (matches recording-callback.js's pattern). No
 * runtime-deps add needed.
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

function formatCallbackNumber(digits) {
  // Light cleanup for the Slack message. Don't try to be too clever;
  // the team will read this manually.
  if (!digits) return null;
  const clean = digits.replace(/\D/g, '');
  if (clean.length === 10) return `+1${clean}`; // assume US
  if (clean.length === 11 && clean.startsWith('1')) return `+${clean}`;
  return `+${clean}`;
}

async function postSlack(webhookUrl, payload) {
  if (!webhookUrl) {
    console.warn('queue-callback-saved: SLACK_WEBHOOK_URL not set — skipping DM');
    return;
  }
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    const resp = await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!resp.ok) {
      console.error(`queue-callback-saved Slack post HTTP ${resp.status}`);
      return;
    }
    console.log('queue-callback-saved Slack DM sent');
  } catch (err) {
    console.error('queue-callback-saved Slack post failed:', err.message);
  }
}

async function postLinear(context, { caller, intent, callId, callbackNumber, recordingUrl, recordingDuration }) {
  const apiKey = context.LINEAR_API_KEY;
  const teamId = context.LINEAR_TEAM_ID;
  if (!apiKey || !teamId) {
    console.warn('queue-callback-saved: LINEAR_API_KEY or LINEAR_TEAM_ID missing — skipping ticket');
    return;
  }
  const title = `Voicemail + callback: ${caller || '(unknown)'}`;
  const recap =
    `**Caller:** ${caller || '(unknown)'}\n\n` +
    `**Intent:** ${intent || '(none captured)'}\n\n` +
    `**Callback number (from keypad):** ${callbackNumber || 'NOT PROVIDED — caller hung up before entering one'}\n\n` +
    `**Voicemail:** ${recordingDuration ? `${recordingDuration}s — ${recordingUrl}.mp3` : 'no audio captured'}\n\n` +
    `**Cartesia call ID:** ${callId || '(none)'}\n\n` +
    `_Logged by Twilio Function (queue-callback-saved.js)._`;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 10000);
    const resp = await fetch(LINEAR_GRAPHQL_URL, {
      method: 'POST',
      headers: { Authorization: apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: CREATE_ISSUE_MUTATION,
        variables: { input: { teamId, title, description: recap } },
      }),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!resp.ok) {
      console.error(`queue-callback-saved Linear post HTTP ${resp.status}`);
      return;
    }
    console.log('queue-callback-saved Linear ticket created');
  } catch (err) {
    console.error('queue-callback-saved Linear post failed:', err.message);
  }
}

exports.handler = async (context, event, callback) => {
  console.log('queue-callback-saved event:', JSON.stringify(event));

  const callId = (event.call_id || '').toString().slice(0, 64);
  const caller = (event.caller || '').toString().slice(0, 20);
  const intent = (event.intent || '').toString().slice(0, 200);
  const recordingUrl = (event.recordingUrl || '').toString();
  const recordingDuration = parseInt(event.recordingDuration || '0', 10);
  const rawDigits = (event.Digits || '').toString();
  const callbackNumber = formatCallbackNumber(rawDigits);

  // Single consolidated Slack DM. Block Kit so the audio link previews
  // inline (Slack auto-unfurls .mp3 URLs).
  const audioBlock = recordingUrl
    ? `*Voicemail (${recordingDuration}s):* ${recordingUrl}.mp3`
    : `*Voicemail:* (no audio captured)`;
  const callbackBlock = callbackNumber
    ? `*Callback number (from keypad):* ${callbackNumber}`
    : `*Callback number:* NOT PROVIDED — caller hung up before entering one`;
  const slackPayload = {
    text: `🎙️ Voicemail + callback request from ${caller || '(unknown)'}`,
    blocks: [
      {
        type: 'header',
        text: { type: 'plain_text', text: '🎙️ Voicemail + callback request' },
      },
      {
        type: 'section',
        fields: [
          { type: 'mrkdwn', text: `*Caller:*\n${caller || '(unknown)'}` },
          { type: 'mrkdwn', text: `*Intent:*\n${intent || '(none captured)'}` },
        ],
      },
      { type: 'section', text: { type: 'mrkdwn', text: callbackBlock } },
      { type: 'section', text: { type: 'mrkdwn', text: audioBlock } },
      {
        type: 'context',
        elements: [
          {
            type: 'mrkdwn',
            text: `Cartesia call ID: \`${callId}\` (audio link works ~30s after this DM lands)`,
          },
        ],
      },
    ],
  };

  // Fire Slack + Linear in parallel — neither blocks the other, and the
  // TwiML response below doesn't wait for either to land (each has its
  // own short timeout).
  await Promise.all([
    postSlack(context.SLACK_WEBHOOK_URL, slackPayload),
    postLinear(context, { caller, intent, callId, callbackNumber, recordingUrl, recordingDuration }),
  ]);

  const twiml = new Twilio.twiml.VoiceResponse();
  twiml.say(
    { voice: 'Polly.Joanna' },
    "Thanks — we'll get back to you as soon as we can. Bye for now."
  );
  twiml.hangup();
  callback(null, twiml);
};
