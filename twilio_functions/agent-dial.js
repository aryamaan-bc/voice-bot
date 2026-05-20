/**
 * Twilio Function — TwiML returned by the TwiML App (TWIML_APP_SID) when
 * the browser Voice SDK calls `device.connect({ params })`.
 *
 * Three modes:
 *   1. mode=queue (v2.1 conference-on-bridge) — rep is taking the next
 *      caller from the bc-support queue. This Function:
 *        a. Looks up the bc-support queue + its front member
 *        b. REST API-dequeues the front member, redirecting them to
 *           /conference-join with a unique conf name (bc-active-XXX)
 *        c. Posts a Slack "active call" message with a "Join this call"
 *           button so a second rep can join as a 3rd party if needed
 *        d. Returns <Dial><Conference>bc-active-XXX</Conference></Dial>
 *           so the rep's browser leg joins the same conference
 *      Recording is conference-scoped (configured on the customer's leg
 *      in /conference-join), captures all parties including any rep
 *      who joins later via the "Join this call" Slack button.
 *
 *   2. mode=conference&conference=<name> (v1 / "Join active call") —
 *      rep joins a specific named conference. Used by:
 *        - Legacy v1 browser-pickup (conference name derived from
 *          caller's number)
 *        - v2.1 "Join this call" Slack button (Rep B joining Rep A's
 *          active conference)
 *
 *   3. No mode (treated as legacy conference) — same as mode=conference.
 *
 * Why we dequeue to a Conference instead of using <Dial><Queue>:
 *   `<Dial><Queue>` is point-to-point (2 parties only). A Conference
 *   accepts any number of participants, so Rep B can join Rep A's
 *   active call (consultation, training, escalation). v2's queue is
 *   only the HOLD-side architecture; the live conversation runs in a
 *   Conference for multi-party support.
 *
 * Source of truth: this repo. Deploy via Twilio Serverless API.
 *
 * IMPORTANT: After deploying, set the TwiML App's Voice Request URL to
 *   https://<functions-domain>/agent-dial  (HTTP POST)
 * It should already point there from earlier setup.
 */
exports.handler = async function (context, event, callback) {
  console.log('agent-dial event:', JSON.stringify(event));
  const twiml = new Twilio.twiml.VoiceResponse();
  const mode = (event.mode || '').toString();

  if (mode === 'queue') {
    // v2.1 conference-on-bridge path. Dequeue head member of the queue
    // and redirect them into a named conference, then return TwiML so
    // the rep joins the same conference.
    const queueName = context.TWILIO_QUEUE_NAME || 'bc-support';
    const client = context.getTwilioClient();

    let queueSid;
    try {
      const queues = await client.queues.list({
        friendlyName: queueName,
        limit: 1,
      });
      if (!queues.length) {
        twiml.say(
          { voice: 'Polly.Joanna' },
          'No callers are waiting in the queue right now. Goodbye.'
        );
        twiml.hangup();
        return callback(null, twiml);
      }
      queueSid = queues[0].sid;
    } catch (err) {
      console.error('agent-dial: queue lookup failed', err.message);
      twiml.say(
        { voice: 'Polly.Joanna' },
        'Something went wrong looking up the queue. Please try again.'
      );
      twiml.hangup();
      return callback(null, twiml);
    }

    // Conference name. Short enough to fit Twilio's 64-char limit;
    // unique per dequeue using millisecond-resolution timestamp +
    // a sliver of randomness so simultaneous reps clicking each get
    // their own conference (even if Twilio's atomic dequeue gave
    // them different customers).
    const confName =
      `bc-active-${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;
    const conferenceJoinUrl =
      `https://${context.DOMAIN_NAME}/conference-join?conf=${encodeURIComponent(confName)}`;

    // Atomically dequeue the front member. Twilio updates the queue
    // member's call leg to execute the TwiML at the supplied URL —
    // which is /conference-join, returning TwiML that puts them in the
    // named conference. After this returns, the customer is in the
    // conference (or about to be); we return TwiML for the rep to
    // join the same one.
    let customerCallSid = null;
    let customerNumber = '(unknown)';
    try {
      const dequeued = await client
        .queues(queueSid)
        .members('Front')
        .update({
          url: conferenceJoinUrl,
          method: 'POST',
        });
      customerCallSid = dequeued.callSid;
      console.log(
        `agent-dial dequeued callSid=${customerCallSid} -> conf=${confName}`
      );
    } catch (err) {
      // 404 commonly means the queue is empty. Other errors: Twilio
      // outage, malformed URL, permission issue.
      console.warn('agent-dial: dequeue failed', err.status, err.message);
      twiml.say(
        { voice: 'Polly.Joanna' },
        err.status === 404
          ? 'No callers are waiting in the queue right now. Goodbye.'
          : 'Something went wrong dequeuing the call. Please try again.'
      );
      twiml.hangup();
      return callback(null, twiml);
    }

    // Best-effort: fetch the customer's caller number for the Slack
    // post. If this fails, we proceed without it (the rep is already
    // being routed to the conference).
    if (customerCallSid) {
      try {
        const call = await client.calls(customerCallSid).fetch();
        customerNumber = call.from || customerNumber;
      } catch (err) {
        console.warn('agent-dial: call fetch failed', err.message);
      }
    }

    // Fire the Slack "active call" notification. The dashboard is now
    // the single rep action surface — this Slack DM is a passive
    // alert with a link to the dashboard (not a direct join button).
    // Eliminates the previous confusion where reps clicked "Join this
    // call" thinking it was "Take next caller".
    if (context.SLACK_WEBHOOK_URL) {
      const dashboardUrl = `https://${context.DOMAIN_NAME}/dashboard.html`;
      const payload = {
        text: `:telephone_receiver: Active call started: ${customerNumber}`,
        blocks: [
          {
            type: 'section',
            text: {
              type: 'mrkdwn',
              text:
                `:telephone_receiver: *Active call started*\n` +
                `*Customer:* ${customerNumber}\n` +
                `*Conference:* \`${confName}\``,
            },
          },
          {
            type: 'actions',
            elements: [
              {
                type: 'button',
                style: 'primary',
                text: { type: 'plain_text', text: 'Open Rep Dashboard' },
                url: dashboardUrl,
              },
            ],
          },
          {
            type: 'context',
            elements: [
              {
                type: 'mrkdwn',
                text:
                  '_Use the dashboard to join this call as a second ' +
                  'rep (consult, escalation, training). The first rep ' +
                  'stays on the call._',
              },
            ],
          },
        ],
      };
      // Fire-and-don't-await — the TwiML response should not be delayed
      // by the Slack post. Twilio Functions will let the promise resolve
      // in the background.
      fetch(context.SLACK_WEBHOOK_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
        .then((r) => console.log(`agent-dial Slack post status=${r.status}`))
        .catch((e) => console.warn('agent-dial Slack post failed:', e.message));
    }

    // Rep joins the conference. Recording is configured on the
    // customer's leg via /conference-join (record-from-start); we don't
    // re-configure it here. endConferenceOnExit:false so this rep
    // leaving doesn't kick the customer out — the customer's leg has
    // endConferenceOnExit:true so the conf only ends when THEY hang up.
    //
    // statusCallback wired on the rep's leg so /conference-status fires
    // when the rep drops. Without this, the customer is stranded in
    // silence (their leg's statusCallback only fires for customer
    // events, not rep events — Twilio statusCallbacks are per-leg).
    twiml.dial().conference(
      {
        startConferenceOnEnter: true,
        endConferenceOnExit: false,
        beep: false,
        statusCallback: `https://${context.DOMAIN_NAME}/conference-status`,
        statusCallbackEvent: 'leave',
        statusCallbackMethod: 'POST',
      },
      confName
    );
    return callback(null, twiml);
  }

  // v1 / legacy / v2.1 "Join active call" path — rep joins a specific
  // named conference. Used for legacy v1 browser pickup AND for v2.1
  // when a second rep clicks the "Join this call" Slack button (the
  // active-call URL has mode=conference&conf=bc-active-XXX).
  const rawConf = (event.conference || '').toString();
  const confName = rawConf.replace(/[^a-zA-Z0-9-]/g, '').slice(0, 64);

  if (!confName) {
    twiml.say(
      { voice: 'Polly.Joanna' },
      'No conference specified. Hanging up.'
    );
    twiml.hangup();
    return callback(null, twiml);
  }

  // Same statusCallback rationale as the mode=queue branch — fires
  // /conference-status when the rep drops so a stranded customer gets
  // the 30s wind-down + auto-hangup.
  twiml.dial().conference(
    {
      startConferenceOnEnter: true,
      endConferenceOnExit: false,
      beep: false,
      statusCallback: `https://${context.DOMAIN_NAME}/conference-status`,
      statusCallbackEvent: 'leave',
      statusCallbackMethod: 'POST',
    },
    confName
  );

  callback(null, twiml);
};
