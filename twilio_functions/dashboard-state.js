/**
 * Twilio Function — JSON state endpoint for the rep dashboard.
 *
 * Returns a snapshot of:
 *   - Queue members (callers waiting in bc-support, sorted by position)
 *   - Active conferences (in-progress calls with rep + customer)
 *
 * Polled by /dashboard.html every 5s.
 *
 * Response shape:
 * {
 *   "fetchedAt": "2026-05-18T18:43:00.000Z",
 *   "queue": [
 *     { position, callSid, callerNumber, waitTimeSeconds }
 *   ],
 *   "activeCalls": [
 *     { conferenceName, conferenceSid, participantCount,
 *       callerNumber, startedAt }
 *   ]
 * }
 *
 * Errors on any sub-fetch are caught and logged; the corresponding
 * field is set to a sensible default ("(unknown)" / 0 / empty array)
 * so the dashboard still renders something instead of blanking out.
 *
 * Cost: ~6-10 Twilio API calls per request (queue lookup + queue
 * members list + N member call fetches + conferences list + per-
 * conference participants + per-participant call fetches). At a 5s
 * dashboard poll cadence × 2 reps = ~3-4 Twilio API calls/sec total —
 * well under the 100 req/sec account limit.
 *
 * Source of truth: this repo. Deploy via Twilio Serverless API.
 */
exports.handler = async function (context, event, callback) {
  const result = {
    fetchedAt: new Date().toISOString(),
    queue: [],
    activeCalls: [],
  };

  const client = context.getTwilioClient();
  const queueName = context.TWILIO_QUEUE_NAME || 'bc-support';

  // === Queue members ===
  try {
    const queues = await client.queues.list({
      friendlyName: queueName,
      limit: 1,
    });
    if (queues.length) {
      const queueSid = queues[0].sid;
      const members = await client.queues(queueSid).members.list({ limit: 20 });
      // Fetch each member's call.from in parallel — bounded by the
      // limit:20 above.
      const memberInfo = await Promise.all(
        members.map(async (m) => {
          let callerNumber = '(unknown)';
          try {
            const call = await client.calls(m.callSid).fetch();
            callerNumber = call.from || callerNumber;
          } catch (e) {
            console.warn(
              `dashboard-state: call.fetch for queue member ${m.callSid} failed: ${e.message}`
            );
          }
          return {
            position: m.position,
            callSid: m.callSid,
            callerNumber,
            waitTimeSeconds: m.waitTime || 0,
          };
        })
      );
      memberInfo.sort((a, b) => a.position - b.position);
      result.queue = memberInfo;
    }
  } catch (e) {
    console.error('dashboard-state: queue list/members fetch failed', e.message);
  }

  // === Active conferences ===
  try {
    const conferences = await client.conferences.list({
      status: 'in-progress',
      limit: 20,
    });
    // For each conference, get participant count + try to identify
    // the customer's caller number. The customer's leg has a phone
    // number `from`; rep legs are `client:bc-agent` (browser SDK
    // identity). We pick the first non-client `from` as the customer.
    const confInfo = await Promise.all(
      conferences.map(async (c) => {
        let participantCount = 0;
        let callerNumber = '(unknown)';
        try {
          const participants = await client
            .conferences(c.sid)
            .participants.list({ limit: 10 });
          participantCount = participants.length;
          for (const p of participants) {
            try {
              const call = await client.calls(p.callSid).fetch();
              if (call.from && !call.from.startsWith('client:')) {
                callerNumber = call.from;
                break;
              }
            } catch (e) {
              // Per-participant fetch failure — try the next.
            }
          }
        } catch (e) {
          console.warn(
            `dashboard-state: participants.list for conf ${c.sid} failed: ${e.message}`
          );
        }
        return {
          conferenceName: c.friendlyName,
          conferenceSid: c.sid,
          participantCount,
          callerNumber,
          startedAt: c.dateCreated && c.dateCreated.toISOString
            ? c.dateCreated.toISOString()
            : null,
        };
      })
    );
    // Newest first (most-recently-started conferences at the top).
    confInfo.sort((a, b) => {
      if (!a.startedAt) return 1;
      if (!b.startedAt) return -1;
      return new Date(b.startedAt) - new Date(a.startedAt);
    });
    result.activeCalls = confInfo;
  } catch (e) {
    console.error('dashboard-state: conferences list failed', e.message);
  }

  // Twilio Functions Response with explicit JSON content-type.
  const response = new Twilio.Response();
  response.appendHeader('Content-Type', 'application/json');
  response.appendHeader('Cache-Control', 'no-store');
  response.setBody(result);
  callback(null, response);
};
