-- v1: tasks, results, approvals, staged orders, audit log (R4 item 4.1).
CREATE TABLE tasks (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    owner        TEXT NOT NULL,
    description  TEXT NOT NULL,
    input_json   TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX ix_tasks_owner ON tasks(owner);
CREATE INDEX ix_tasks_status ON tasks(status);

CREATE TABLE results (
    task_id      TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    finished_at  TEXT NOT NULL
);

CREATE TABLE approvals (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    step_id       TEXT NOT NULL,
    tool_id       TEXT NOT NULL,
    request_json  TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    status        TEXT NOT NULL,
    decided_by    TEXT,
    decided_at    TEXT,
    comment       TEXT
);
CREATE INDEX ix_approvals_status ON approvals(status);

CREATE TABLE staged_orders (
    staging_id    TEXT PRIMARY KEY,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE audit_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    subject       TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL
);
