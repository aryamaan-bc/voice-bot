# Production deploy runbook — v2 hold queue (Twilio Enqueue)

**Read this entire file before running any command.** This deploy
touches production. Many steps are irreversible without manual cleanup
or work to roll back. Each phase has explicit verification steps and a
rollback path; do not skip them.

The goal: bring the queue-v1 branch's v2 hold-queue work (Twilio
Enqueue with hold music + position updates + press-1 voicemail intake)
to the production Cartesia agent (`agent_CicivQhXS56dgUehm3B1Ea`,
`+1 (888) 460-4901`) and the production Twilio Functions service
(`ZSe76103f244f13fa11a0276e282f87b3b`, `bc-voice-functions-8157.twil.io`).
Staging (`agent_sxQV2ZUGSBN8KY8uQKsSr2`, `+1 (572) 218-0660`,
`ZSac4ea69969563d790da8d975a76b969c`) is the reference — staging works
end-to-end as of commit `e23a4f3`.

**Estimated time:** 30-45 minutes of attentive work. Plan to be at the
keyboard the whole time; do not start during peak call hours.

---

## Resource inventory (verify each before starting)

| Thing | Production value |
|---|---|
| Cartesia agent | `agent_CicivQhXS56dgUehm3B1Ea` (bc-faq-agent) |
| Inbound phone | `+1 (888) 460-4901` (toll-free) |
| Twilio Functions service | `ZSe76103f244f13fa11a0276e282f87b3b` |
| Functions domain | `bc-voice-functions-8157.twil.io` |
| Functions env SID | `ZEbacf3b74def328b3abda838d42d9a720` |
| TwiML App (browser pickup) | `APab507ce8dea4efcf213a0ccbd5b1...` (in prod Functions env as `TWIML_APP_SID`) |
| API Key (Voice SDK signing) | `SK4c69fab42a3d91758b77721adbd9...` (in prod Functions env as `TWILIO_API_KEY_SID`) |
| Slack channel | `#bc_customer_calls` (webhook in prod Functions env) |
| Linear team | Same as staging (creds NOT yet in prod Functions env — added in Phase 2) |
| Git branch (target) | `main` |
| Prod worktree path | `/Users/aryamaanlakhotia/Downloads/voice-bot1` |
| Prod `.env` path | `/Users/aryamaanlakhotia/Downloads/voice-bot1/.env` |
| Conference-join number (legacy/v1 only) | `+1 (917) 979-6392` (unused in v2 mode but stays configured) |

| Production Function SIDs (existing — to be UPDATED with new versions) | |
|---|---|
| conference-join.js | `ZHf1e1603c6f397ba3144b6eb512545148` |
| recording-callback.js | `ZHd9ec473e646b70fe454ff6121a344e17` |
| agent-token.js | `ZHff80de095594fd10cc8cdba7bcdc5b2d` |
| agent-dial.js | `ZH7ee81ea8c24fc1e1a0dc3105f3f90ddf` |
| probe-accept.js | `ZHce19091449717c1ff1ea938bac394e34` |
| agent-pickup.html (Asset) | `ZH147329922be542a34e16c8f6a30acbda` |

---

## Drift warning before you start

The prod Functions service is **stale relative to local** — three
Functions have unsynced changes from earlier work (per STAGING.md
"Drift surfaced during setup" section). The local versions on the
queue-v1 branch incorporate:
- `agent-pickup.html` — adds `.caller` info panel + `.fallback.urgent`
  red-banner styles + Case 6 watchdog UI + new `mode=queue` UI
- `conference-join.js` — adds `?conf=` query param handling + Twilio
  API caller-ID fallback
- `recording-callback.js` — adds `event.customer` handling for Slack
  DM + new `type=queue_bridged` template
- `agent-dial.js` — adds `mode=queue` branch for v2 queue dispatch

**Uploading the new versions will overwrite prod's stale versions with
the up-to-date local versions.** Effects on existing v1 behavior:
- Pre-existing browser pickup flow: still works. The new code adds
  conditional branches (`if mode === "queue"`); the default unset
  case keeps v1 conference behavior identical.
- Conference recording Slack DM: now mentions the customer's number
  (was missing on stale prod). Improvement, not regression.
- Browser pickup page: now shows the urgent red banner if WebRTC hangs
  10s + caller info. Improvement.

You will land all three improvements as a side effect of this deploy.

---

## Phase 0: Pre-flight checks (read-only — runs in 1 minute)

Run these from the staging worktree
(`/Users/aryamaanlakhotia/Downloads/voice-bot1-staging`).

### 0.1 — Confirm prod resource state

```bash
set -a && source /Users/aryamaanlakhotia/Downloads/voice-bot1/.env && set +a

# Verify prod Cartesia agent ID
test "$CARTESIA_AGENT_ID" = "agent_CicivQhXS56dgUehm3B1Ea" \
  && echo "✓ Prod CARTESIA_AGENT_ID matches" \
  || echo "✗ CARTESIA_AGENT_ID mismatch — STOP"

# Verify prod Functions service is up
curl -s -o /dev/null -w "Functions service HTTP %{http_code}\n" \
  "https://bc-voice-functions-8157.twil.io/agent-pickup.html"

# Verify prod Cartesia agent's latest deploy is healthy (pinned version
# must be "Ready" and all 3 regions "deployed")
cartesia status agent_CicivQhXS56dgUehm3B1Ea 2>&1 | head -15

# List prod's current Function versions (to confirm SIDs match the
# inventory above + capture the latest version SID per function for the
# Build step in Phase 2.4 — write these down)
PROD_FN_SERVICE=ZSe76103f244f13fa11a0276e282f87b3b
for FN_SID in ZHf1e1603c6f397ba3144b6eb512545148 ZHd9ec473e646b70fe454ff6121a344e17 \
              ZHff80de095594fd10cc8cdba7bcdc5b2d ZH7ee81ea8c24fc1e1a0dc3105f3f90ddf \
              ZHce19091449717c1ff1ea938bac394e34; do
  curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/$FN_SID/Versions?PageSize=1" \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
v = d.get('function_versions',[{}])[0]
print(f\"  {v.get('path','?')}: latest version {v.get('sid','NONE')}\")"
done
```

**STOP if any check fails.** Don't continue with mismatched IDs.

### 0.2 — Note current prod Cartesia version (for rollback)

```bash
cartesia status agent_CicivQhXS56dgUehm3B1Ea 2>&1 | grep "Pinned Version"
```

**Write down the Pinned Version SID** (e.g. `av_XXXX`). If anything
goes wrong, rollback is: pin back to that version.

### 0.3 — Confirm git state

```bash
cd /Users/aryamaanlakhotia/Downloads/voice-bot1-staging
git status   # should show no uncommitted changes other than .cartesia/config.toml
git log --oneline -5  # most recent commit should be e23a4f3 (or later v2 work)

cd /Users/aryamaanlakhotia/Downloads/voice-bot1
git status   # prod worktree — should be clean on main
git log --oneline -3  # most recent should be 6822ab8 or later
```

**STOP if either worktree has uncommitted changes other than
.cartesia/config.toml** — clean those up first.

---

## Phase 1: Merge queue-v1 into main

This brings all queue work (v1 + v2, both code paths gated by
`QUEUE_VERSION`) onto the `main` branch. v1 stays as the rollback path;
v2 ships behind `QUEUE_ENABLED=false` initially.

### 1.1 — Merge

```bash
cd /Users/aryamaanlakhotia/Downloads/voice-bot1
git status   # confirm clean
git fetch origin   # if you push/pull from a remote; harmless if not
git merge queue-v1 --no-ff -m "Merge queue-v1 to main — v2 hold queue with Twilio Enqueue"
```

**Expected output:** fast-forward or clean merge commit. **STOP if any
merge conflict** — back out (`git merge --abort`) and resolve with the
staging worktree's contents (queue-v1 is the source of truth for
overlapping files).

### 1.2 — Sanity check

```bash
git log --oneline -10
# You should see all the v2 slice commits + earlier v1 commits

ls hold_queue.py twilio_functions/queue-*.js
# All 6 new Twilio Functions + hold_queue.py should be present
```

### 1.3 — Rollback for Phase 1

If something broke: `git reset --hard ORIG_HEAD` (returns main to the
pre-merge state). Only safe before Phase 5 (Cartesia deploy).

---

## Phase 2: Update prod Functions service

The same Twilio Serverless API pattern as staging: upload new versions,
build with all versions (existing + new), deploy.

### 2.1 — Add the new env vars to prod Functions service

These are needed by the new Functions (queue-action.js posts to Linear
directly; queue-wait.js reads MAX_QUEUE_WAIT_SECONDS; etc.).

```bash
set -a && source /Users/aryamaanlakhotia/Downloads/voice-bot1/.env && set +a
PROD_FN_SERVICE=ZSe76103f244f13fa11a0276e282f87b3b
PROD_ENV_SID=ZEbacf3b74def328b3abda838d42d9a720

for KV in \
  "TWILIO_QUEUE_NAME=bc-support" \
  "MAX_QUEUE_WAIT_SECONDS=900" \
  "LINEAR_API_KEY=$LINEAR_API_KEY" \
  "LINEAR_TEAM_ID=$LINEAR_TEAM_ID"; do
  K="${KV%%=*}"
  V="${KV#*=}"
  curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    -X POST "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Environments/$PROD_ENV_SID/Variables" \
    --data-urlencode "Key=$K" \
    --data-urlencode "Value=$V" \
    | python3 -c "
import json, sys
d=json.load(sys.stdin); print(f\"  {d.get('key','ERR')} -> {d.get('sid', d.get('message'))}\")"
done
```

**Verify:** all four should show ZV-prefixed SIDs, not error messages.

Note: `HOLD_MUSIC_URL` is intentionally NOT set — the queue-wait.js
fallback `https://demo.twilio.com/docs/classic.mp3` (verified working)
plays when this env var is absent.

### 2.2 — Upload the 6 NEW Functions

These don't exist on prod yet, so we create each Function resource +
upload its first Version.

```bash
cd /Users/aryamaanlakhotia/Downloads/voice-bot1
mkdir -p /tmp/prod_fn_versions
> /tmp/prod_fn_versions/new.txt
for NAME in enqueue-customer queue-wait queue-action queue-press queue-after-record queue-callback-saved; do
  echo "--- $NAME ---"
  FN_RESP=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    -X POST "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions" \
    --data-urlencode "FriendlyName=$NAME")
  FN_SID=$(echo "$FN_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['sid'])")
  VER_RESP=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    -X POST "https://serverless-upload.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/$FN_SID/Versions" \
    -F "Path=/$NAME" -F "Visibility=public" \
    -F "Content=@twilio_functions/$NAME.js;type=application/javascript")
  VER_SID=$(echo "$VER_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('sid','ERR'))")
  echo "  FN_SID=$FN_SID  VER_SID=$VER_SID"
  echo "$VER_SID" >> /tmp/prod_fn_versions/new.txt
done
```

**Verify:** 6 ZN-prefixed version SIDs written to
`/tmp/prod_fn_versions/new.txt`. If any errored, STOP — investigate the
specific Function's HTTP response.

### 2.3 — Upload UPDATED versions of the 3 modified existing Functions + 1 modified Asset

```bash
# agent-dial.js (FN_SID is fixed — from inventory)
NEW_AGENT_DIAL_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless-upload.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/ZH7ee81ea8c24fc1e1a0dc3105f3f90ddf/Versions" \
  -F "Path=/agent-dial" -F "Visibility=public" \
  -F "Content=@twilio_functions/agent-dial.js;type=application/javascript" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sid'])")
echo "agent-dial new ver: $NEW_AGENT_DIAL_VER"

# recording-callback.js
NEW_REC_CB_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless-upload.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/ZHd9ec473e646b70fe454ff6121a344e17/Versions" \
  -F "Path=/recording-callback" -F "Visibility=public" \
  -F "Content=@twilio_functions/recording-callback.js;type=application/javascript" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sid'])")
echo "recording-callback new ver: $NEW_REC_CB_VER"

# conference-join.js (sync the stale prod version — this is the
# unsynced commit e361236 work that STAGING.md flagged)
NEW_CJ_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless-upload.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/ZHf1e1603c6f397ba3144b6eb512545148/Versions" \
  -F "Path=/conference-join" -F "Visibility=public" \
  -F "Content=@twilio_functions/conference-join.js;type=application/javascript" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sid'])")
echo "conference-join new ver: $NEW_CJ_VER"

# agent-pickup.html (Asset, not Function — different endpoint)
NEW_PICKUP_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless-upload.twilio.com/v1/Services/$PROD_FN_SERVICE/Assets/ZH147329922be542a34e16c8f6a30acbda/Versions" \
  -F "Path=/agent-pickup.html" -F "Visibility=public" \
  -F "Content=@twilio_functions/agent-pickup.html;type=text/html" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['sid'])")
echo "agent-pickup.html new ver: $NEW_PICKUP_VER"
```

### 2.4 — Capture latest versions for the 2 UNCHANGED functions

`agent-token.js` and `probe-accept.js` are unchanged but the Build
needs to reference some version of each. Use the LATEST existing
version (no upload needed).

```bash
LATEST_AGENT_TOKEN_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/ZHff80de095594fd10cc8cdba7bcdc5b2d/Versions?PageSize=1" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['function_versions'][0]['sid'])")
LATEST_PROBE_VER=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Functions/ZHce19091449717c1ff1ea938bac394e34/Versions?PageSize=1" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['function_versions'][0]['sid'])")
echo "agent-token (unchanged) latest ver: $LATEST_AGENT_TOKEN_VER"
echo "probe-accept (unchanged) latest ver: $LATEST_PROBE_VER"
```

### 2.5 — Create Build + Deployment

Build needs:
- 6 NEW Function versions (from 2.2)
- 3 MODIFIED Function versions (from 2.3)
- 2 UNCHANGED Function versions (from 2.4)
- 1 MODIFIED Asset version (from 2.3)

```bash
# Combine all 11 Function versions (6 new from /tmp/prod_fn_versions/new.txt +
# 3 modified + 2 unchanged)
NEW_VERS=$(cat /tmp/prod_fn_versions/new.txt | xargs)
ALL_FUNC_VERS="$NEW_VERS $NEW_AGENT_DIAL_VER $NEW_REC_CB_VER $NEW_CJ_VER $LATEST_AGENT_TOKEN_VER $LATEST_PROBE_VER"

# Build the curl flags
FN_FLAGS=""
for V in $ALL_FUNC_VERS; do FN_FLAGS="$FN_FLAGS -d FunctionVersions=$V"; done

BUILD_RESP=$(curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Builds" \
  $FN_FLAGS \
  -d "AssetVersions=$NEW_PICKUP_VER" \
  --data-urlencode 'Dependencies=[{"name":"@twilio/runtime-handler","version":"2.0.3"},{"name":"twilio","version":"5.0.3"},{"name":"xmldom","version":"0.6.0"},{"name":"lodash","version":"4.17.21"},{"name":"util","version":"0.12.5"}]')
PROD_BUILD_SID=$(echo "$BUILD_RESP" | python3 -c "import json,sys; print(json.load(sys.stdin).get('sid','ERR'))")
echo "PROD_BUILD_SID=$PROD_BUILD_SID"

# Wait for the build to complete (typically 30-90s)
until curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Builds/$PROD_BUILD_SID/Status" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status']); exit(0 if d['status'] in ('completed','failed') else 1)" 2>/dev/null; do sleep 3; done

# Deploy the build to the prod environment
curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Environments/$PROD_ENV_SID/Deployments" \
  --data-urlencode "BuildSid=$PROD_BUILD_SID" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('Deploy:', d.get('sid', d.get('message')))"
```

**STOP if Build status is `failed`** — query the Build resource for
errors and investigate before deploying.

### 2.6 — Smoke-test prod Functions endpoints

Test each new endpoint returns valid TwiML. **No real call yet** —
just curl the prod Functions domain directly.

```bash
DOMAIN=bc-voice-functions-8157.twil.io

echo "--- /enqueue-customer ---"
curl -s -X POST "https://$DOMAIN/enqueue-customer?call_id=t&caller=%2B1&intent=t" | head -3

echo "--- /queue-wait at QueueTime=0 ---"
curl -s -X POST "https://$DOMAIN/queue-wait?call_id=t&caller=%2B1&intent=t" \
  --data-urlencode "QueueTime=0" --data-urlencode "QueuePosition=1" | head -3

echo "--- /queue-wait at QueueTime=1000 (hard-timeout — should return Leave) ---"
curl -s -X POST "https://$DOMAIN/queue-wait?call_id=t&caller=%2B1&intent=t" \
  --data-urlencode "QueueTime=1000" --data-urlencode "QueuePosition=1" | head -3

echo "--- /agent-dial mode=queue ---"
curl -s -X POST "https://$DOMAIN/agent-dial" --data-urlencode "mode=queue" | head -3

echo "--- /agent-dial conference=test (LEGACY — must still work) ---"
curl -s -X POST "https://$DOMAIN/agent-dial" --data-urlencode "conference=bc-test-99" | head -3

echo "--- /agent-pickup.html?mode=queue ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "https://$DOMAIN/agent-pickup.html?mode=queue"

echo "--- /agent-pickup.html?conf=bc-test-99 (LEGACY — must still work) ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "https://$DOMAIN/agent-pickup.html?conf=bc-test-99"
```

**Expected:**
- `/enqueue-customer` → `<Say>` + `<Enqueue waitUrlMethod=...>bc-support</Enqueue>`
- `/queue-wait` at t=0 → `<Gather input="dtmf">` with Say + Play
- `/queue-wait` at t=1000 → `<Leave/>`
- `/agent-dial` with mode=queue → `<Dial record=...><Queue>bc-support</Queue></Dial>`
- `/agent-dial` with conference=... → `<Dial><Conference>bc-test-99</Conference></Dial>` (legacy v1 still works)
- Both `/agent-pickup.html` paths → HTTP 200

**STOP if anything returns 500 or unexpected XML.** Twilio Functions
side must be solid before you touch the Cartesia agent.

### 2.7 — Rollback for Phase 2

Twilio Serverless keeps every prior deployment. To roll back to the
previous prod build:

```bash
# List previous deployments
curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Environments/$PROD_ENV_SID/Deployments?PageSize=5" \
  | python3 -m json.tool | head -30

# Re-deploy a prior build (replace ZB_OLD_BUILD_SID with the previous one)
curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -X POST "https://serverless.twilio.com/v1/Services/$PROD_FN_SERVICE/Environments/$PROD_ENV_SID/Deployments" \
  --data-urlencode "BuildSid=ZB_OLD_BUILD_SID"
```

The new Functions (enqueue-customer etc.) won't physically be deleted,
but the build pointed at will no longer route to them. The original 5
prod functions return to their pre-deploy versions.

---

## Phase 3: Add queue env vars to prod .env

Edit `/Users/aryamaanlakhotia/Downloads/voice-bot1/.env` and append the
following block (do NOT delete or modify existing variables — only add):

```
# ─── Hold queue (v1 silent hold; feature-flagged) ─────────────────────────
# Master flag. Start with false so v2 lands dormant; flip to true after
# Phase 7 verification confirms the queue path works.
QUEUE_ENABLED=false

# v2 (Twilio Enqueue with hold music) is the active path when enabled.
# Set to v1 for instant rollback to the in-Cartesia silent-hold queue —
# cartesia env set is enough, no code redeploy needed.
QUEUE_VERSION=v2

# Per-process capacity (Taylor + Aryamaan). v1 path uses this for
# slot accounting; v2 path doesn't track it (Twilio queue serializes).
MAX_CONCURRENT_REPS=2

# Hard-timeout safety floor (15 min). After this, the queue caller is
# kicked to the voicemail-intake flow automatically.
MAX_QUEUE_WAIT_SECONDS=900

# v1-only knobs (unused when QUEUE_VERSION=v2; kept for rollback).
QUEUE_POLL_INTERVAL_SECONDS=15
QUEUE_POSITION_UPDATE_INTERVAL_SECONDS=45
QUEUE_CHECKIN_INTERVAL_SECONDS=180
```

**Note:** `PROBE_TIMEOUT_SECONDS` is not set here — defaults to 60s in
code, which is the production-appropriate value. (Staging set it to
180s for solo-testing convenience.)

**Verify:**

```bash
grep -E "QUEUE|MAX_CONCURRENT" /Users/aryamaanlakhotia/Downloads/voice-bot1/.env
# Should print exactly the 7 lines above.
```

---

## Phase 4: Push env vars to prod Cartesia agent

```bash
cartesia env set --from=/Users/aryamaanlakhotia/Downloads/voice-bot1/.env \
                 --agent-id=agent_CicivQhXS56dgUehm3B1Ea 2>&1 | tail -5
```

**Expected:** `Successfully set N environment variable(s)` where N
matches the count in your `.env` (~25 vars). This auto-triggers a
deploy of the existing pinned code with the new env. Don't panic — the
new env vars are passive (QUEUE_ENABLED=false means none of the queue
code runs).

### 4.1 — Verify the env push didn't break anything

```bash
cartesia status agent_CicivQhXS56dgUehm3B1Ea 2>&1 | head -10
# Status should still be "Ready" after the auto-triggered redeploy.
```

If the agent becomes `Failed` here, **STOP and roll back env**: revert
the .env changes and `cartesia env set` again. The agent should
recover within ~30s.

---

## Phase 5: Deploy queue-v1 code to prod Cartesia agent

This is the irreversible step in terms of Cartesia code state.
(Reversible by re-deploying a prior version, but you should not need
to.)

### 5.1 — Sanity check the prod worktree is on main and has v2 code

```bash
cd /Users/aryamaanlakhotia/Downloads/voice-bot1
git log --oneline -3
# Should show the merge commit + v2 commits like e23a4f3, 4bad279, etc.

ls hold_queue.py twilio_functions/queue-*.js
# All 6 new queue-* Functions + hold_queue.py should exist
```

### 5.2 — Deploy via the rsync-to-tmp workaround

The Cartesia CLI has a known bug where deploying from inside a git
worktree only uploads 2 files. The prod worktree is NOT a worktree (it
has a real `.git/` directory), so this should be fine — but use the
rsync pattern anyway for consistency with how staging deploys.

```bash
cd /Users/aryamaanlakhotia/Downloads/voice-bot1
D=$(mktemp -d)/src \
  && rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
       --exclude='*.egg-info' ./ "$D"/ \
  && cartesia deploy "$D" --agent-id=agent_CicivQhXS56dgUehm3B1Ea --verbose 2>&1 | tail -25 \
  && rm -rf "$(dirname "$D")"
```

**Expected:** "Uploading archive (~80 KB)" + "Deployment created
successfully!" + a `Check deployment progress:` line with the new
av_XXX SID.

### 5.3 — Wait for the deploy to be Ready

```bash
# Replace av_XXX with the version SID from the previous step's output
until cartesia status av_XXX 2>&1 | grep -q "Ready\|Failed"; do sleep 5; done
cartesia status av_XXX 2>&1 | head -10
```

**STOP if Status is `Failed`** — investigate via Cartesia dashboard.
Common causes: missing env var, Python import error, dependency
mismatch.

### 5.4 — Rollback for Phase 5

If the deploy is bad:

```bash
# List recent deploys
cartesia status agent_CicivQhXS56dgUehm3B1Ea 2>&1
# Find the previous Pinned Version SID (the one you wrote down in Phase 0.2).
# Pin it via the Cartesia dashboard or CLI — the CLI does not have a
# direct "pin" command, so use the dashboard:
# https://play.cartesia.ai/agents/agent_CicivQhXS56dgUehm3B1Ea
```

---

## Phase 6: Initial verification with QUEUE_ENABLED=false

The queue is still disabled — v1 conference-based browser pickup
should be unchanged from pre-deploy.

### 6.1 — Test call (1 caller, no queue activity)

Dial `+1 (888) 460-4901` from a test phone:

- [ ] Bot greets normally (Cartesia voice — same as before)
- [ ] Ask an FAQ ("What's the 401(k) contribution limit?") → bot
      answers correctly
- [ ] Ask for a human ("Can I speak to a representative?") → bot
      escalates with the OLD wording (LLM-supplied announcement, NOT
      the v2 hardcoded "Transferring you now") because QUEUE_ENABLED is
      false → the v1 probe path runs
- [ ] Slack DM should land in `#bc_customer_calls` with "Take call in
      browser" button. Button URL has `?conf=bc-XXXXXXXXXX` (legacy
      v1 format), NOT `?mode=queue`
- [ ] If a rep clicks the button within 60s → caller is bridged to the
      conference. Same behavior as before this deploy.
- [ ] If no rep clicks within 60s → caller falls through to "Sorry,
      all our lines are busy" + callback intake. Same as before.

**STOP if any of these regressed.** Phase 5 deploy is wrong — roll
back via Cartesia dashboard.

### 6.2 — Verify no new Twilio errors

```bash
set -a && source /Users/aryamaanlakhotia/Downloads/voice-bot1/.env && set +a
curl -s -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  "https://monitor.twilio.com/v1/Alerts?PageSize=5" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
for a in d.get('alerts', [])[:5]:
    print(f\"  {a['date_created']} | {a['error_code']} | {a['log_level']} | url={(a.get('request_url') or '')[:80]}\")"
```

**Expected:** any alerts older than the deploy timestamp are fine.
No new 11200 / 12200 / 12300 alerts after the deploy.

---

## Phase 7: Enable v2 (flip QUEUE_ENABLED=true)

Now activate the queue path.

### 7.1 — Edit prod .env

Change one line in `/Users/aryamaanlakhotia/Downloads/voice-bot1/.env`:

```
QUEUE_ENABLED=false   →   QUEUE_ENABLED=true
```

### 7.2 — Push the env update

```bash
cartesia env set --from=/Users/aryamaanlakhotia/Downloads/voice-bot1/.env \
                 --agent-id=agent_CicivQhXS56dgUehm3B1Ea 2>&1 | tail -5
```

Auto-triggers a new deploy (env-only change; same code). Wait until
the new pinned version is Ready (~30-60s).

### 7.3 — Test call (v2 path active)

Dial `+1 (888) 460-4901`:

- [ ] FAQ greeting unchanged
- [ ] Ask for a human →
- [ ] Cartesia voice: "Transferring you to our team now — you'll hear
      our hold music in just a moment." (v2 hardcoded announcement)
- [ ] ~3 seconds silence
- [ ] Polly voice: "Putting you on hold. Stay on the line — or press 1
      anytime to leave a message instead."
- [ ] Classical hold music starts
- [ ] Slack DM lands in `#bc_customer_calls` with **"Take next caller"**
      button (NOT "Take call from +1xxx" — that's v1 wording)
- [ ] Rep clicks → bridged to caller via `<Dial><Queue>bc-support</Queue>`

### 7.4 — Test press-1

Place a test call, get to the music, **press 1 on your keypad**:

- [ ] Polly: "Sure — leave a message after the tone..."
- [ ] Record a 5-10s message, press # (or hang up)
- [ ] Polly: "Got it. Now please enter your callback number..."
- [ ] Enter `5551234567#` on the keypad
- [ ] Polly: "Thanks — we'll get back to you as soon as we can."
- [ ] Call ends
- [ ] **ONE consolidated Slack DM** lands in `#bc_customer_calls` with
      both the voicemail audio link AND the callback number
- [ ] **ONE Linear ticket** lands with `outcome=voicemail_logged`

### 7.5 — Test queue-depth-2

Have one rep busy on a real call (or dial from one device + click
"Take next caller" from another to keep a rep occupied). Then dial a
second time from another device:

- [ ] Second caller hears Polly: "You're number 1 in line — thanks for
      holding." (Twilio queue serializes — first caller is now bridged
      to rep, second is at position 1)
- [ ] (If you can manage a third concurrent: third caller hears
      "You're number 2 in line.")
- [ ] When the rep on the bridged call hangs up, another rep can click
      "Take next caller" → bridges to the next in queue

### 7.6 — Verify no Twilio errors

Same alerts check as Phase 6.2. No new 11200 / 12200 / 12300 after the
v2 test call.

---

## Phase 8: Burn-in

Leave prod on v2 for a few real calls / a day. Monitor:

- Slack `#bc_customer_calls` for queue pings + voicemail DMs
- Linear ticket outcomes — distribution should be similar to v1 except
  with new `voicemail_logged` and `abandoned_in_queue` outcomes
- Twilio alerts (Monitor → Alerts in console) — should be quiet

If anything looks off:

```bash
# Instant rollback: flip QUEUE_VERSION back to v1
# Edit /Users/aryamaanlakhotia/Downloads/voice-bot1/.env:
#   QUEUE_VERSION=v2   →   QUEUE_VERSION=v1
cartesia env set --from=/Users/aryamaanlakhotia/Downloads/voice-bot1/.env \
                 --agent-id=agent_CicivQhXS56dgUehm3B1Ea
```

v1 (in-Cartesia silent-hold queue) takes over within ~30s. No code
redeploy needed; both paths are in the same binary.

Or roll back FURTHER with `QUEUE_ENABLED=false` — disables queueing
entirely, returns to the pre-queue probe-based flow.

---

## Cleanup / things to track post-deploy

- **Custom hold music** — replace the Twilio-default classical track
  with a branded MP3. Upload as an Asset to the prod Functions service;
  set `HOLD_MUSIC_URL` env var to the asset URL. Out of scope for this
  initial rollout.

- **Twilio signature validation** on Functions — pre-existing gap.
  Worth fixing in a separate hardening pass.

- **The duplicate escalate_to_human race** — Haiku sometimes emits the
  tool twice in one turn. Defended-against by the phase guard in v2,
  but a tighter atomic check-and-set would eliminate the race. Track
  separately.

- **Eventually delete v1 code** — after weeks of stable v2 burn-in,
  remove `hold_queue.py` + the v1 branches in `escalation.py`. Don't
  rush — v1 is the rollback path.

---

## Quick reference — exact rollback paths

| Phase | What to revert | Command |
|---|---|---|
| 1 (git merge) | The merge commit | `cd /Users/aryamaanlakhotia/Downloads/voice-bot1 && git reset --hard ORIG_HEAD` |
| 2 (Functions deploy) | Prior Functions deployment | Re-deploy old Build SID via Twilio Serverless API (see 2.7) |
| 3-4 (env vars) | Remove queue vars from .env | Edit .env, delete the QUEUE_* lines, `cartesia env set` again |
| 5 (Cartesia code deploy) | Prior pinned version | Pin via Cartesia dashboard with the SID from Phase 0.2 |
| 7 (flag flip) | Flip back to v1 / disable | `QUEUE_VERSION=v1` or `QUEUE_ENABLED=false` + `cartesia env set` |

---

## Summary checklist (high-level)

- [ ] Phase 0: pre-flight checks all green
- [ ] Phase 0.2: prior pinned version SID written down
- [ ] Phase 1: git merge queue-v1 → main succeeds, no conflicts
- [ ] Phase 2.1: 4 new env vars on prod Functions service
- [ ] Phase 2.2: 6 new Functions uploaded (version SIDs captured)
- [ ] Phase 2.3: 3 Functions + 1 Asset updated (version SIDs captured)
- [ ] Phase 2.5: Build + Deploy successful on prod Functions service
- [ ] Phase 2.6: all 7 smoke-test endpoints return expected TwiML
- [ ] Phase 3: queue vars added to prod .env
- [ ] Phase 4: env push to Cartesia successful, agent still Ready
- [ ] Phase 5: code deploy to Cartesia successful, all 3 regions deployed
- [ ] Phase 6: test call with QUEUE_ENABLED=false → v1 behavior unchanged
- [ ] Phase 7: QUEUE_ENABLED flipped to true
- [ ] Phase 7.3-7.5: test calls confirm v2 path works end-to-end
- [ ] Phase 8: burn-in
