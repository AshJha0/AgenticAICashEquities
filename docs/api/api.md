# API

Run with `ceap serve` (or `uvicorn ceap.api.app:app`). Interactive docs at `/docs`.

## Authentication and roles

Send `X-API-Key: <key>`. Keys map to roles via `CEAP_API_KEYS` (`key:role,...`). Development
defaults:

| Key | Role | Capabilities |
|---|---|---|
| `dev-viewer-key` | viewer | `tools:read`, `investigate:read`, `research:read` |
| `dev-trader-key` | trader | + `investigate`, `risk:medium` |
| `dev-quant-key` | quant | + `research`, `data:export` |
| `dev-admin-key` | admin | + `tools:write`, `risk:high`, `approvals:decide`, `trading:execute` |

`research` starts research tasks; `trading:execute` (with `tools:write`) is required to request
paper order staging - the only non-read-only tool - and even then the harness asks a human first.

## Endpoints

| Method | Path | Capability | Description |
|---|---|---|---|
| GET | `/health` | – | version, environment, LLM client, MCP servers, tool count |
| GET | `/metrics` | – | Prometheus text exposition |
| GET | `/tools` | any key | discovered tool catalogue with read-only flag and risk level |
| GET | `/scenarios?all=false` | – | scenario templates (or all 50 with `all=true`) |
| POST | `/investigations` | `investigate` | start an investigation (202 Accepted) |
| GET | `/investigations` | `investigate:read` | list investigations with status |
| GET | `/investigations/{id}` | `investigate:read` | status (`RUNNING`, `PENDING`, `COMPLETED`, `FAILED`) |
| GET | `/investigations/{id}/report` | `investigate:read` | the report (409 while running) |
| GET | `/investigations/{id}/result?include_trace=true` | `investigate:read` | full result: plan, findings, evidence, policy log, step results, trace |
| GET | `/investigations/{id}/trace` | `investigate:read` | spans, state history, policy log, step results |
| POST | `/investigations/{id}/cancel` | `investigate` | cooperative cancellation |
| GET | `/research-scenarios?all=false` | – | research scenario templates (or all 21 with `all=true`) |
| POST | `/research` | `research` | start a signal research task (202 Accepted); 403 when `stage_orders` is requested without `trading:execute` |
| GET | `/research` | `research:read` | list research tasks with status |
| GET | `/research/{id}` | `research:read` | status |
| GET | `/research/{id}/report` | `research:read` | the proposal (`kind: research`, `proposal`) |
| GET | `/research/{id}/result?include_trace=true` | `research:read` | full result |
| GET | `/research/{id}/trace` | `research:read` | spans, state history, policy log, step results (incl. `gov-approval`, `gov-stage-approval`, `gov-stage-orders`) |
| POST | `/research/{id}/cancel` | `research` | cooperative cancellation |
| GET | `/approvals` | `approvals:decide` | pending human approvals (`CEAP_AUTO_APPROVE=false`) - research proposals carry `arguments.proposal` (verdict, flags, scores, rationale) and `arguments.target_portfolio`; staging approvals carry `arguments.orders_preview` |
| POST | `/approvals/{id}` | `approvals:decide` | `{"approved": true, "comment": "..."}` |

Investigation and research ids live in separate namespaces: a research task is 404 under
`/investigations/{id}` and vice versa.

## Creating an investigation

```json
POST /investigations
{
  "question": "Why did our execution quality for AAPL deteriorate between 14:00 and 15:00?",
  "dataset": "T05",
  "symbol": null,
  "window_start": null,
  "window_end": null,
  "baseline_start": null,
  "baseline_end": null,
  "session_date": null
}
```

* `symbol` and the window are parsed from the question when omitted (ticker in the supported
  universe; "between 14:00 and 15:00", "from 2pm to 3:30pm"). Naive times are Europe/London.
* `baseline_*` default to the window of equal length immediately before the investigation window.
* `dataset` selects the scenario data (T01–T10 templates, S01–S50 concrete scenarios). The
  request is validated against the dataset's symbol and coverage before it is accepted (422).

Response:

```json
{
  "task_id": "TASK-128d4c23",
  "status": "RUNNING",
  "symbol": "AAPL",
  "window_start": "2026-09-18T14:00:00+01:00",
  "window_end": "2026-09-18T15:00:00+01:00",
  "baseline_start": "2026-09-18T13:00:00+01:00",
  "baseline_end": "2026-09-18T14:00:00+01:00",
  "dataset": "T05",
  "links": {"status": "...", "report": "...", "trace": "..."}
}
```

## Creating a research task

```json
POST /research
{
  "question": "Does 12-1 momentum work on R01? Evaluate it out-of-sample with costs.",
  "signal": null,
  "dataset": null,
  "start": null,
  "end": null,
  "in_sample_end": null,
  "rebalance_days": null,
  "long_short": true,
  "gross_notional": 50000000.0,
  "stage_orders": false
}
```

* `signal` (`momentum_12_1`, `reversal_5`, `low_vol_60`) and `dataset` (R01–R07, RS01–RS21) are
  parsed from the question when omitted ("momentum", "reversal", "low vol"; "R04"); dates as
  "from YYYY-MM-DD to YYYY-MM-DD". `start`, `end` and `in_sample_end` default to the dataset's
  walk-forward split (one warm-up year, two in-sample years, one out-of-sample year);
  `rebalance_days` defaults to the signal's.
* The request is validated before it is accepted: unknown signal or dataset, a window outside
  coverage or inside the warm-up, an `in_sample_end` outside the window → 422; a principal without
  `research`, or `stage_orders` without `trading:execute` → 403.

Response: `task_id`, `status`, `signal`, `dataset`, `start`, `end`, `in_sample_end`,
`rebalance_days`, `long_short`, `stage_orders`, `links`.

## Report schema (`GET /investigations/{id}/report` and `GET /research/{id}/report`)

| Field | Content |
|---|---|
| `title`, `executive_summary`, `conclusion` | narrative sections |
| `primary_observations` | statements of the top findings |
| `alternative_explanations` | rejected or weak hypotheses with scores |
| `attribution` | `primary`, `secondary`, `deteriorated`, `is_delta_bps`, `vwap_slippage_delta_bps`, ranked cause scores with rationale and metrics |
| `metrics` | `execution.window`, `execution.baseline` (full `ExecutionMetrics`), `market.*` (`MarketStatistics`), `engineering` summary |
| `findings` | id, statement, supporting/contradicting evidence ids, confidence, category, producer, attributes |
| `evidence` | id, type, source, description, timestamp, attributes (tool, arguments, digest) |
| `critique` | assessments per finding, contradictions, unsupported ids, `narrative_number_warnings`, `narrative_evidence_warnings`, LLM model |
| `narrative` | the full text report |
| `kind` | `investigation` or `research` |
| `proposal` | research only: `verdict`, `flags`, `scores`, `rationale`, `signal`, `dataset`, `window`, `target_portfolio`, `approval` (request id, decided_by, comment), `staging` (`requested`, `staged`, `staging_id`, `count`, `buy_notional`, `sell_notional`, `status`, `error`) |

For research reports `metrics` holds `signal_statistics`, `backtest` and `risk` (exposure, limit
checks, stress) and `attribution` is empty.

## Errors

| Status | Meaning |
|---|---|
| 401 | missing or invalid API key |
| 403 | role lacks the capability (incl. `stage_orders` without `trading:execute`) |
| 404 | unknown investigation / research task / approval, or an id from the other namespace |
| 409 | report requested while running; approvals automatic in this environment |
| 422 | request could not be understood or validated (symbol, window, dataset coverage; signal, research window); a failed task's report (e.g. a declined proposal) |
