-- v2: LLM call records, trial ledger, idempotency keys (R4 items 4.1 and 4.4).
CREATE TABLE llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT,
    purpose       TEXT NOT NULL,
    model         TEXT NOT NULL,
    prompt_hash   TEXT NOT NULL,
    request_json  TEXT,
    response_json TEXT,
    at            TEXT NOT NULL
);
CREATE INDEX ix_llm_calls_task ON llm_calls(task_id);

CREATE TABLE trial_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_hash  TEXT NOT NULL,
    params_hash   TEXT NOT NULL,
    sharpe        REAL,
    record_json   TEXT NOT NULL,
    at            TEXT NOT NULL,
    UNIQUE (dataset_hash, params_hash)
);

CREATE TABLE idempotency_keys (
    principal     TEXT NOT NULL,
    key           TEXT NOT NULL,
    body_hash     TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (principal, key)
);

ALTER TABLE results ADD COLUMN trace_json TEXT;
