# Staging environment — bc-faq-staging

Set up 2026-05-15. Lives in parallel with production; no shared mutable state.

## Resources (do not confuse with production)

| Thing | Staging | Production (DO NOT TOUCH) |
|---|---|---|
| Cartesia agent | `agent_sxQV2ZUGSBN8KY8uQKsSr2` (bc-faq-staging) | `agent_CicivQhXS56dgUehm3B1Ea` (bc-faq-agent) |
| Inbound phone number | `+1 (572) 218-0660` | `+1 (888) 460-4901` (toll-free) |
| Conference-join number (for AgentTransferCall fallback) | `+1 (572) 212-4636` (PN068bc1cd51bbb9c8944781f64fc57314) | `+1 (917) 979-6392` |
| Twilio Functions service | `ZSac4ea69969563d790da8d975a76b969c` | `ZSe76103f244f13fa11a0276e282f87b3b` |
| Functions domain | `bc-voice-functions-staging-9498.twil.io` | `bc-voice-functions-8157.twil.io` |
| TwiML App (browser pickup) | `AP3ca09807d76c65f550a1f5683d72623e` | (separate, in prod) |
| API Key (Voice SDK signing) | `SK_REDACTED_see_Twilio_Console` | (separate, in prod) |
| Slack destination | DM to Aryamaan | `#bc_customer_calls` |
| Linear team | Same workspace; tag tickets with `[STAGING]` in recap | Same workspace |
| Git branch | `queue-v1` (this worktree) | `main` |

## What's set up vs what's pending

**Done:**
- New Cartesia agent created and bought number imported
- Standalone Twilio Functions service deployed. Current state: **13
  Functions + 3 Assets** (grew from 4 + 1 as the v2/v2.1/v2.2/v2.4
  slices landed). Inventory:
  - Functions: `conference-status`, `dashboard-state`,
    `queue-callback-saved`, `queue-after-record`, `queue-press`,
    `queue-leave`, `queue-action`, `queue-wait`, `enqueue-customer`,
    `agent-dial`, `agent-token`, `recording-callback`, `conference-join`
  - Assets: `agent-pickup.html`, `dashboard.html`, `classic-60s.mp3`
- TwiML App for browser pickup created, pointed at staging `/agent-dial`
- New API Key for staging Voice SDK token signing
- 4 environment variables set on the staging Functions service
  (`TWILIO_QUEUE_NAME`, `MAX_QUEUE_WAIT_SECONDS`, `LINEAR_API_KEY`,
  `LINEAR_TEAM_ID`) plus `SLACK_WEBHOOK_URL` for recording-callback
- Twilio Sync default service in use for `bc-call-intent` Map (auto-
  created on first `/enqueue-customer` write)
- All staging endpoints smoke-tested and working end-to-end through
  the press-1 voicemail flow.

## Drift vs production (substantial — addressed by PROD_DEPLOY.md)

As of the v2.4 + intent-on-dashboard work, staging has **9 Functions
and 2 Assets that don't exist on prod at all**, plus modifications to
4 prod files. Prod has only the 5 original Functions
(`agent-dial`, `agent-token`, `recording-callback`, `conference-join`,
`probe-accept`) + 1 Asset (`agent-pickup.html`).

Net prod-side changes when PROD_DEPLOY.md is executed:
- **9 new Functions to create**: `enqueue-customer`, `queue-wait`,
  `queue-action`, `queue-press`, `queue-after-record`,
  `queue-callback-saved`, `queue-leave`, `conference-status`,
  `dashboard-state`
- **2 new Assets to upload**: `dashboard.html`, `classic-60s.mp3`
- **3 modified Functions**: `agent-dial`, `recording-callback`,
  `conference-join`
- **1 modified Asset**: `agent-pickup.html`
- **1 unchanged on both sides**: `agent-token`, `probe-accept`

`probe-accept` is a v1-only Function that staging never imported. It
should stay deployed on prod (rollback path for `QUEUE_VERSION=v1`)
but its source isn't in `twilio_functions/`. If `QUEUE_VERSION=v1` is
ever flipped on prod, that Function continues to serve from its
existing prod version.

**This drift is intentional** and gets resolved by following
`PROD_DEPLOY.md`. It is not a follow-up task — it IS the rollout.

## Phased rollout (from the plan)

Plan file: `~/.claude/plans/crystalline-sleeping-aho.md`.

### v1 (silent hold) — landed on staging

1. **Slice 0** — Docs + prompt updates (CLAUDE.md, README.md). Done.
2. **Slice 1** — Add `queue_waiting` + `abandoned_in_queue` to `Outcome` literal. Done.
3. **Slice 2** — Refactor `escalation_status["in_progress"]: bool` → `phase: str` enum. Done.
4. **Slice 3** — `hold_queue.py` with `_QUEUE`, `_QUEUE_LOCK`, `_ACTIVE_PROBES`, shared poller. Done.
5. **Slice 4** — Queue admission + silent hold + dispatch. Done.
6. **Slice 4.5** — Conversational hold (LLM dispatch during queue_wait, record_followup opt-out). Done.
7. **Hybrid long-wait UX** — periodic check-ins + 15-min safety floor. Done. Deployed to staging at `MAX_CONCURRENT_REPS=1` for solo testing.

### v2 (Twilio Enqueue with hold music) — in progress

User feedback after v1 staging burn-in: callers expect real hold music, not silence + TTS position updates. Cartesia Line SDK has no audio-injection event, so the call must move out to Twilio for the wait. v2 architecture is documented in the plan.

v1 is **not deleted** — `QUEUE_VERSION` env var (default `v2` after rollout) selects which implementation runs. Rollback = `QUEUE_VERSION=v1` + `cartesia env set` (no code redeploy).

| Slice | Files | What changes | Status |
|-------|-------|--------------|--------|
| v2-0 | CLAUDE.md, README.md, STAGING.md | Doc updates: v2 architecture + QUEUE_VERSION rollback. Zero runtime change. | Done |
| v2-1 | 6 NEW Functions (`enqueue-customer`, `queue-wait`, `queue-action`, `queue-press`, `queue-after-record`, `queue-callback-saved`) | Deploy 6 new Twilio Functions. Chained voicemail→gather flow → one consolidated Slack DM. | Done |
| v2-2 | `agent-dial.js`, `agent-pickup.html`, `recording-callback.js` (MODIFIED) | Add `mode=queue` branch. Backward-compatible. | Done |
| v2-3 | `linear_ticket.py` | Add `voicemail_logged` outcome. | Done |
| v2-4 | `escalation.py`, `main.py`, `slack_ticket.py` | Wire v2 alongside v1, gated by `QUEUE_VERSION`. v1 code preserved untouched. | Done |
| v2-6 | `.env.staging.local`, staging Functions service env | Set queue env vars, push Cartesia env, deploy. | Done |
| v2-7 | (manual) | Initial burn-in. Hit live music URL crash, Rick Astley file, voicemail flow bugs — all fixed. | Done |
| **v2.1** | `agent-dial.js`, `conference-join.js` | Conference-on-bridge: rep dequeues into per-call `bc-active-<id>` conference. Multiple reps can join the same active call. | Done |
| **v2.2** | NEW `dashboard-state.js` + `dashboard.html`; modified `escalation.py`, `agent-dial.js` Slack DMs | Rep dashboard replaces noisy per-caller Slack DMs. Polls JSON state every 5s. | Done |
| **v2.3** | `agent-pickup.html` | Mute button (`call.mute(bool)` from Voice JS SDK). | Done |
| **v2.4** | NEW `conference-status.js`; modified `conference-join.js` | Auto-hangup: if the only rep leaves the conference, customer hears 30s grace period then a goodbye + hangup. | Done |
| **Intent on dashboard** | `enqueue-customer.js` (Sync write), `dashboard-state.js` (Sync read), `dashboard.html` (render) | Twilio Sync Map `bc-call-intent` keyed by CallSid. Dashboard shows "Wants: …" under each card. | Done |
| **Voicemail flow polish** | NEW `queue-leave.js`; modified `queue-wait.js`, `queue-press.js` | Press-1 now does `<Leave/>` first so the caller actually exits the queue before the recording flow starts (was looping back to position updates mid-voicemail). Prompt no longer invites hangup. | Done |
| v2-8 | prod | Mirror Functions + Assets + env to prod, set `QUEUE_ENABLED=true` on prod. See PROD_DEPLOY.md. | Pending |

Slice v2-5 (delete v1 code) is intentionally **skipped** — v1 stays as the rollback path until v2 has weeks of solid burn-in. Cleanup happens in a separate later PR.

## QUEUE_VERSION rollback runbook (v2 only)

If v2 misbehaves on staging or production:

```bash
# Edit .env (or .env.staging.local for staging) — change one line:
QUEUE_VERSION=v1   # was v2

# Push to Cartesia. Auto-creates a new deployment.
cartesia env set --from=.env.staging.local --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2
# (or for prod: --agent-id=agent_CicivQhXS56dgUehm3B1Ea)
```

No code redeploy needed — both v1 and v2 implementations are in the same binary. Effective within ~30 seconds. To re-enable v2 after fixes, flip back.

## Deploy commands (staging only)

**Known CLI bug, must be worked around**: `cartesia deploy` invoked inside a
git worktree (where `.git` is a file pointer, not a directory) only uploads
2 files (`.cartesia/config.toml` + `.env.example`) instead of the full
source tree. The CLI's git integration silently breaks on worktrees and
the build then fails. Workaround is to rsync to a tmp dir without `.git`
and deploy from there.

```bash
# 1. Push env vars (works fine from worktree; doesn't use the broken path):
cartesia env set --from=.env.staging.local --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2

# 2. Deploy code via the worktree workaround:
D=$(mktemp -d)/src \
  && rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
       --exclude='*.egg-info' ./ "$D"/ \
  && cartesia deploy "$D" --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2 \
  && rm -rf "$(dirname "$D")"

# 3. Watch staging logs during testing:
cartesia logs --follow --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2

# 4. Sanity check what agent is attached to which number:
cartesia agents ls
```

If/when the Cartesia CLI is fixed for worktrees, the deploy step collapses
back to `cartesia deploy --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2` in the
worktree directly. Until then, the rsync-to-tmp dance is mandatory.

Files explicitly excluded by the rsync match `.gitignore`'s spirit but go
further: `.git` (the worktree pointer is the bug trigger), `.venv` /
`__pycache__` / `*.egg-info` (local Python build state, would inflate the
archive). `.env.*.local` files are already covered by `.gitignore` and the
Cartesia CLI honors that even without `.git` present (verified during
setup — `.env.staging.local` was NOT in the test deploy archive).

## Workflow guidelines

- **Branch hygiene**: all queue work goes on `queue-v1` in this worktree. Don't touch `main` from here. When a slice is ready to promote: `git checkout main` in the other checkout, merge `queue-v1`, deploy to prod.
- **Every slice deploys to staging first**. Burn in for a day or two before promoting.
- **Production deploy = explicit only**. No automation should ever `cartesia deploy --agent-id=agent_CicivQhXS56dgUehm3B1Ea`. If you're touching the prod agent, you should be reading this line and making a conscious choice.
- **Don't sync local Functions to prod casually**. The drift is real but addressing it is a separate task. Leave prod's Functions alone unless explicitly told to update them.
- **`cartesia.toml` in this worktree**: writes the staging agent ID into `[app]`. Don't `cartesia init` in the prod checkout by mistake — that would overwrite prod's config.
- **Anthropic + Linear + Slack keys are shared across staging/prod**. A leak of one affects both. Don't print them to logs.

## Verification checklist for the first call to +1 (572) 218-0660

- [ ] Bot answers with the standard greeting ("Hey, thanks for calling Basic Capital…")
- [ ] FAQ question (e.g., "What's the 401(k) limit?") answers correctly
- [ ] Asking for a human triggers an escalation → Slack DM appears in your private channel (NOT in `#bc_customer_calls`)
- [ ] The Slack DM has a "Take call in browser" button; click it → `agent-pickup.html` from `bc-voice-functions-staging-9498.twil.io` opens with caller info + Join button
- [ ] After hours (when this is read, depending on time): outside-hours greeting plays
- [ ] Linear ticket appears for the call

If any of these fail, check `cartesia logs --follow --agent-id=agent_sxQV2ZUGSBN8KY8uQKsSr2`.

## Slice 2 scope note

`escalation_status["in_progress"]` is touched by 5+ call sites across `main.py`, `slack_ticket.py`, and `escalation.py`. When Slice 2 refactors it to `phase`, all of those have to migrate in the same PR. Grep for `in_progress` early to verify the scope before starting.
