# CEAP operations runbook

What to do when the API misbehaves. Each section is: the symptom, how to confirm it, what to do,
and why. Commands assume the container image from `Dockerfile` or a local install (`ceap` on the
path) and the database at `$CEAP_DB_PATH`.

The alert rules that point here are in `deploy/prometheus/ceap-alerts.yml`; a reverse-proxy example
is in `deploy/compose/`. Neither is a production deployment: they show the shape the app expects.

## Contents

- [Restart](#restart)
- [Stuck task](#stuck-task)
- [Stuck approval](#stuck-approval)
- [Event-loop lag](#event-loop-lag)
- [Audit chain break](#audit-chain-break)
- [Disk full or disk I/O error](#disk-full-or-disk-io-error)
- [API keys](#api-keys)
- [Paper-day replay gate](#paper-day-replay-gate)

## Restart

**Plain idea.** A restart is always safe: nothing is left half-decided.

**What happens.** At start-up `Platform.recover()` (`src/ceap/platform.py`) marks every task the
previous process left `PENDING` or `RUNNING` as `INTERRUPTED`, and every approval it left `PENDING`
as `EXPIRED`, each with an audit entry. A decision posted after the restart for an expired approval
gets HTTP 409, never a silent loss. Completed tool calls of an interrupted run are in the run
journal (table `run_journal`, 0.9.0 G5): re-running the same task id reads them back instead of
calling the tools again, and an approval already decided for the same content is not asked again
(`src/ceap/harness/durable.py`).

**Do.**

1. `ceap db status` - schema version, size, audit chain state.
2. Restart the container (`docker compose restart ceap`) or the process.
3. `GET /health` must answer 200 within the start period (20 s in the image's `HEALTHCHECK`).
4. Look for `task.interrupted` and `approval.expired` audit entries: those are the casualties to
   tell their owners about.

## Stuck task

**Symptom.** `CeapStuckTasks` or `CeapInvestigationsFailing` fires; a task stays `RUNNING`.

**Confirm.** `GET /investigations/{id}` (or `/research/{id}`) shows the state; the trace shows the last span. A task waiting for a
human is in `AWAITING_APPROVAL`, which is not stuck: see [Stuck approval](#stuck-approval).

**Do.**

1. Every task has a hard deadline (`CEAP_TASK_TIMEOUT_SECONDS`) and every step a timeout
   (`CEAP_STEP_TIMEOUT_SECONDS`); a task past its deadline fails by itself. If one does not, that is a bug:
   capture `/metrics` and the trace first.
2. Cancel it: `POST /investigations/{id}/cancel`. Cancellation is cooperative and races every tool await, so
   it returns within one step.
3. If the event loop is blocked, cancellation cannot run either: see
   [Event-loop lag](#event-loop-lag), then [Restart](#restart).

## Stuck approval

**Symptom.** `CeapApprovalsSwept` fires, or `GET /approvals` lists an old request.

**What happens.** A waiting step expires its own approval after `CEAP_APPROVAL_TIMEOUT_S`. If the
waiter died without a restart (an exception path, a cancelled task), the row would stay `PENDING`
for ever. Since 0.9.0 (G8) a sweep in the API `lifespan` runs every
`CEAP_APPROVAL_SWEEP_S` seconds (default 60) and expires every `PENDING` row older than the timeout
plus one sweep interval that no live waiter in this process holds. Each expiry is audited
(`approval.expired`, reason beginning `ttl sweep`) and counted in `ceap_approvals_swept_total`.

**Do.** Nothing, usually: tell the requester to submit again. A steady rate of sweeps means waiters
are dying: look for `investigation failed` log lines around the request time.

## Event-loop lag

**Symptom.** `CeapEventLoopLag`: the mean lag of the asyncio loop is above 250 ms for 5 minutes.

**What it measures.** A monitor task sleeps 0.5 s in a loop and records how late it wakes up
(`ceap_event_loop_lag_seconds`, a summary; `src/ceap/api/loopmon.py`). On an idle, healthy loop the
lag is about a millisecond. A long lag means something synchronous is running on the loop: a
CPU-bound tool not declared `cpu_bound`, a blocking file read, a slow SQLite call outside the
writer thread.

**Do.**

1. Look at `ceap_tool_latency_seconds_max` per tool and the traces of tasks running at the time.
2. If one tool is the cause, mark it `cpu_bound` (it then runs in the worker-process pool) or move
   its I/O to `asyncio.to_thread`.
3. As a stop-gap, lower `CEAP_MAX_CONCURRENT_INVESTIGATIONS`.

## Audit chain break

**Symptom.** `ceap db verify-chain` exits 1.

**Why it matters.** Every audit row is linked to the previous one by an HMAC keyed outside the
database (migration 0004). A break means a row was changed, deleted or inserted by something other
than the platform, or the chain key changed.

**Do.**

1. Halt new paper orders: `ceap db kill-switch set --reason "audit chain break"`. The switch stops
   the paper job and `stage_orders` (it does not block other writes, and is itself audited).
2. Take a copy for forensics: `ceap db backup /safe/place/ceap-$(date +%F).db`.
3. Compare against the last anchor: `ceap db verify-chain --anchor var/audit_head.json`. Rows before
   the anchored head that no longer verify were changed after the anchor was written.
4. Check the chain key (`CEAP_AUDIT_HMAC_KEY`; retired keys in `CEAP_AUDIT_HMAC_OLD_KEYS`) is the one the database was written
   with: a wrong key breaks every link, a tampered row breaks one.
5. Do not "repair" the chain. Record the incident and start a new database if the old one cannot be
   trusted; keep the old file.

## Disk full or disk I/O error

**Symptom.** `/health` reports `write_probe_ok: false`; logs show `disk I/O error`,
`database or disk is full` or `DatabaseTimeoutError`.

**What happens.** Every write goes through one writer thread in one transaction
(`src/ceap/persistence/db.py`), so a failed write rolls back whole: no half-written task or
approval. The request that hit it fails with 5xx; the next one tries again. The behaviour under an
injected I/O error is tested in `tests/unit/test_r090_chaos.py`.

**Do.**

1. Free space or grow the volume. `ceap db prune` applies the retention settings
   (`CEAP_RETENTION_*_DAYS`) and the size bound (`CEAP_DB_MAX_BYTES`); the audit log is never
   pruned.
2. `ceap db status` must report the expected schema version and an intact chain.
3. [Restart](#restart) if the write probe does not recover by itself.

## API keys

Since 0.9.0 (H11) a raw key in `CEAP_API_KEYS` must carry at least 256 bits (32 bytes) of estimated
entropy outside dev, and the platform refuses to start otherwise. Generate one with

    python -c "import secrets; print(secrets.token_urlsafe(32))"

(43 characters from a 64-symbol alphabet: 43 x 6 = 258 bits). Prefer pre-hashed keys in
`CEAP_API_KEY_HASHES`; an entry may name its key with `id=<name>`, and the principal is then
labelled `<role>:<name>` in audit rows instead of the hash-derived `<role>:xxxxxxxx`.

## Paper-day replay gate

`ceap.harness.paper_gate` (0.9.0, G11) recomputes a finished paper day from the raw feed and diffs
it with what the ledger recorded. A non-empty diff means the recorded day is not reproducible from
its inputs: do not publish the forward log until the cause is known. The status page source for
Pages is written by the same module (`render_status_page`).
