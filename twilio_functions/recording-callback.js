/**
 * Twilio Function — receives Twilio's recordingStatusCallback POST when
 * a conference recording finishes processing. Posts a Slack DM with the
 * recording's listen URL and duration so ops doesn't have to dig into
 * Twilio Console to know a call recording is ready.
 *
 * Twilio POSTs these fields (subset we use):
 *   - RecordingUrl, RecordingSid, RecordingDuration (seconds)
 *   - ConferenceSid, AccountSid
 *
 * The raw RecordingUrl requires Basic-Auth with Twilio creds to play —
 * Slack users can't open it directly. We send a Twilio Console URL
 * instead (after login, plays in browser) plus the raw URL for tooling.
 *
 * Best-effort: if Slack is down or unconfigured we log and return 200 so
 * Twilio doesn't retry endlessly.
 *
 * Source of truth: this repo. Deploy via Twilio Console → Functions →
 * bc-voice-functions → /recording-callback, visibility Public.
 */
exports.handler = async function (context, event, callback) {
  const slackWebhook = context.SLACK_WEBHOOK_URL;
  if (!slackWebhook) {
    console.warn('SLACK_WEBHOOK_URL env var not set — skipping Slack DM');
    return callback(null, '');
  }

  const recordingUrl = event.RecordingUrl || '';
  const recordingSid = event.RecordingSid || '';
  const conferenceSid = event.ConferenceSid || 'n/a';
  const customerNumber = event.customer || '(unknown)';
  const durationSec = parseInt(event.RecordingDuration, 10) || 0;
  const minutes = Math.floor(durationSec / 60);
  const seconds = durationSec % 60;
  const durationLabel = `${minutes}m ${seconds}s`;

  const consoleUrl = recordingSid
    ? `https://console.twilio.com/us1/monitor/logs/call-recordings/${recordingSid}`
    : '';
  const directMp3 = recordingUrl ? `${recordingUrl}.mp3` : '';

  const linkLine = consoleUrl
    ? `<${consoleUrl}|Listen in Twilio Console> (login required)`
    : '(recording URL missing from callback)';

  const payload = {
    text: `:tape: Conference recording ready — caller ${customerNumber} — ${durationLabel}`,
    blocks: [
      {
        type: 'header',
        text: { type: 'plain_text', text: ':tape: Conference recording' },
      },
      {
        type: 'section',
        fields: [
          { type: 'mrkdwn', text: `*Caller:*\n${customerNumber}` },
          { type: 'mrkdwn', text: `*Duration:*\n${durationLabel}` },
        ],
      },
      {
        type: 'section',
        text: { type: 'mrkdwn', text: linkLine },
      },
      {
        type: 'context',
        elements: [
          {
            type: 'mrkdwn',
            text:
              `Conference SID: \`${conferenceSid}\`  ·  ` +
              `Recording SID: \`${recordingSid}\`  ·  ` +
              `Direct mp3 (auth): ${directMp3}`,
          },
        ],
      },
    ],
  };

  try {
    const response = await fetch(slackWebhook, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const body = await response.text();
      console.warn(`Slack returned ${response.status}: ${body}`);
    }
  } catch (e) {
    console.warn('Slack post failed:', e);
  }

  callback(null, '');
};
