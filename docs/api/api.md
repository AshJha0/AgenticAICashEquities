# API

Run with `ceap serve` (or `uvicorn ceap.api.app:app`). Interactive docs at `/docs`.

## Authentication and roles

Send `X-API-Key: <key>`. Keys map to roles via `CEAP_API_KEYS` (`key:role,...`). Development
defaults:

| Key | Role | Capabilities |
|---|---|---|
| `dev-viewer-key` | viewer | `tools:read`, `investigate:read` |
| `dev-trader-key` | trader | + `investigate`, `risk:medium` |
| `dev-quant-key` | quant | + `data:export` |
| `dev-admin-key` | admin | + `tools:write`, `risk:high`, `approvals:decide` |

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
| GET | `/approvals` | `approvals:decide` | pending human approvals (`CEAP_AUTO_APPROVE=false`) |
| POST | `/approvals/{id}` | `approvals:decide` | `{"approved": true, "comment": "..."}` |

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

## Report schema (`GET /investigations/{id}/report`)

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

## Errors

| Status | Meaning |
|---|---|
| 401 | missing or invalid API key |
| 403 | role lacks the capability |
| 404 | unknown investigation / approval |
| 409 | report requested while running; approvals automatic in this environment |
| 422 | request could not be understood or validated (symbol, window, dataset coverage) |
