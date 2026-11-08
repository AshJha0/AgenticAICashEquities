-- v3: paper trading (RP). Append-only, hash-chained ledger, daily snapshots, forward and simulated logs.
-- Every table refuses UPDATE and DELETE: corrections are new rows, never edits.
CREATE TABLE paper_ledger (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    book          TEXT NOT NULL,
    date          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    order_id      TEXT,
    symbol        TEXT,
    quantity      REAL,
    price         REAL,
    cash_delta    REAL NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0,
    payload_json  TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE
);
CREATE INDEX ix_paper_ledger_book ON paper_ledger(book, seq);

CREATE TABLE paper_days (
    book          TEXT NOT NULL,
    date          TEXT NOT NULL,
    equity        REAL NOT NULL,
    snapshot_json TEXT NOT NULL,
    state_json    TEXT NOT NULL,
    ledger_head   TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (book, date)
);

CREATE TABLE paper_forward_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL UNIQUE,
    row_json      TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE,
    recorded_at   TEXT NOT NULL
);

CREATE TABLE paper_simulated_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    book          TEXT NOT NULL,
    date          TEXT NOT NULL,
    row_json      TEXT NOT NULL,
    prev_hash     TEXT NOT NULL,
    hash          TEXT NOT NULL UNIQUE,
    recorded_at   TEXT NOT NULL,
    UNIQUE (book, date)
);

CREATE TRIGGER paper_ledger_no_update BEFORE UPDATE ON paper_ledger
BEGIN SELECT RAISE(ABORT, 'paper_ledger is append-only'); END;
CREATE TRIGGER paper_ledger_no_delete BEFORE DELETE ON paper_ledger
BEGIN SELECT RAISE(ABORT, 'paper_ledger is append-only'); END;
CREATE TRIGGER paper_days_no_update BEFORE UPDATE ON paper_days
BEGIN SELECT RAISE(ABORT, 'paper_days is append-only'); END;
CREATE TRIGGER paper_days_no_delete BEFORE DELETE ON paper_days
BEGIN SELECT RAISE(ABORT, 'paper_days is append-only'); END;
CREATE TRIGGER paper_forward_log_no_update BEFORE UPDATE ON paper_forward_log
BEGIN SELECT RAISE(ABORT, 'paper_forward_log is append-only'); END;
CREATE TRIGGER paper_forward_log_no_delete BEFORE DELETE ON paper_forward_log
BEGIN SELECT RAISE(ABORT, 'paper_forward_log is append-only'); END;
CREATE TRIGGER paper_simulated_log_no_update BEFORE UPDATE ON paper_simulated_log
BEGIN SELECT RAISE(ABORT, 'paper_simulated_log is append-only'); END;
CREATE TRIGGER paper_simulated_log_no_delete BEFORE DELETE ON paper_simulated_log
BEGIN SELECT RAISE(ABORT, 'paper_simulated_log is append-only'); END;
