/**
 * Twilio Function — Conference status webhook handler.
 *
 * Registered as the `statusCallback` URL on the Conference verb in
 * conference-join.js and agent-dial.js (mode=queue branch). Fires on
 * Conference participant events (we subscribe to 'leave' only).
 *
 * Purpose: when a rep leaves the conference and the customer is now
 * alone, give a brief grace period (30s) for another rep to (re)join,
 * then end the customer's call gracefully. Without this Function the
 * customer is stranded in an empty conference forever (or until they
 * hang up themselves), because the conference is configured with
 * `endConferenceOnExit:false` on rep legs so multi-rep consultation
 * works (any rep leaving doesn't kick the customer).
 *
 * Behavior:
 *   1. Twilio POSTs a `leave` event when any participant leaves.
 *   2. Function fetches the leaver's Call resource and checks `from`.
 *      Reps have `from = client:bc-agent` (browser SDK identity).
 *      Customers have a phone-number `from` (e.g. +1xxxxxxxxxx).
 *   3. If the customer left → ignore (conference already ends naturally
 *      via endConferenceOnExit:true on their leg).
 *   4. If a rep left → list remaining participants. Identify the
 *      customer (the non-client `from`). Count remaining reps.
 *   5. If reps > 0 (multi-rep conversation) → do nothing.
 *   6. If reps == 0 (rep left, customer alone) → REST API-update the
 *      customer's call to a brief "rep stepped away" message + 30s
 *      pause + final goodbye + hangup. The customer's call leg leaves
 *      the conference at this point; they hear a clean wind-down
 *      instead of dead air.
 *
 * Trade-off: during the 30s pause the customer is OUT of the conference
 * (their TwiML changed). A rep clicking "Join this call" in those 30s
 * lands in an empty conference. For the BC deployment (2 reps, rare
 * intentional drops) this is acceptable; the rep can call the customer
 * back from the Linear/Slack ticket if needed.
 */
exports.handler = async function (context, event, callback) {
  // Twilio sends an empty body but TwiML expectation is text/plain.
  const responseEmpty = (msg) => {
    console.log(msg);
    callback(null, '');
  };

  const evt = (event.StatusCallbackEvent || '').toString();
  if (evt !== 'leave') {
    return responseEmpty(
      `conference-status: ignoring event=${evt} (not 'leave')`
    );
  }

  const confSid = event.ConferenceSid;
  const leavingCallSid = event.CallSid;
  if (!confSid || !leavingCallSid) {
    return responseEmpty(
      'conference-status: missing ConferenceSid or CallSid — ignoring'
    );
  }

  const client = context.getTwilioClient();

  // Step 1 — identify the leaver. Was it a rep (client:bc-agent) or
  // the customer (phone number)?
  let leaverFrom = '';
  try {
    const leaverCall = await client.calls(leavingCallSid).fetch();
    leaverFrom = (leaverCall.from || '').toString();
  } catch (e) {
    return responseEmpty(
      `conference-status: leaver call.fetch failed: ${e.message}`
    );
  }

  if (!leaverFrom.startsWith('client:')) {
    return responseEmpty(
      `conference-status: customer left (from=${leaverFrom}); ` +
      `conference ends naturally via endConferenceOnExit, nothing to do`
    );
  }

  // Step 2 — a rep left. List remaining participants in the conference.
  let participants;
  try {
    participants = await client
      .conferences(confSid)
      .participants.list({ limit: 10 });
  } catch (e) {
    return responseEmpty(
      `conference-status: participants.list failed: ${e.message}`
    );
  }

  // Step 3 — classify remaining participants. The customer is the one
  // whose Call.from is a phone number (not client:bc-agent).
  let customerCallSid = null;
  let remainingReps = 0;
  await Promise.all(
    participants.map(async (p) => {
      try {
        const c = await client.calls(p.callSid).fetch();
        const f = (c.from || '').toString();
        if (f && !f.startsWith('client:')) {
          customerCallSid = p.callSid;
        } else if (f.startsWith('client:')) {
          remainingReps++;
        }
      } catch (e) {
        console.warn(
          `conference-status: skipping participant ${p.callSid}: ${e.message}`
        );
      }
    })
  );

  if (remainingReps > 0) {
    return responseEmpty(
      `conference-status: ${remainingReps} other rep(s) still on call; not ending`
    );
  }

  if (!customerCallSid) {
    return responseEmpty(
      'conference-status: no customer remains (already left?); nothing to do'
    );
  }

  // Step 4 — customer is alone. Update their call with a graceful
  // wind-down: brief Say, 30s Pause, final goodbye, Hangup. They leave
  // the conference and hear this TwiML instead of dead air.
  const twiml =
    '<?xml version="1.0" encoding="UTF-8"?>' +
    '<Response>' +
      '<Say voice="Polly.Joanna">' +
        'Looks like the rep had to step away for a moment. Hang tight while I check.' +
      '</Say>' +
      '<Pause length="30"/>' +
      '<Say voice="Polly.Joanna">' +
        "Sorry, I couldn't reconnect you to the rep. We'll follow up with you. Goodbye." +
      '</Say>' +
      '<Hangup/>' +
    '</Response>';

  try {
    await client.calls(customerCallSid).update({ twiml });
    console.log(
      `conference-status: customer ${customerCallSid} alone after rep ` +
      `left conf ${confSid} — moved to 30s wait-then-hangup TwiML`
    );
  } catch (e) {
    console.error(
      `conference-status: customer update failed: ${e.message}`
    );
  }

  callback(null, '');
};
