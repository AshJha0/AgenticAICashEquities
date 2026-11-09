-- v5 (0.9.0, G5): step-boundary journal of harness runs, keyed by the ToolRequest idempotency key.
CREATE TABLE run_journal (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    key           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    step_id       TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    UNIQUE (task_id, key)
);
CREATE INDEX ix_run_journal_task ON run_journal(task_id);
