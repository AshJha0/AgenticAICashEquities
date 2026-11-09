-- v4 (0.8.0): HMAC-keyed hash chains, chain epochs, DB-backed kill switch (items A6, F11).
-- Existing rows keep hash_alg 'sha256' and still verify; new rows are written as 'hmac-sha256:<key id>'.
-- ADD COLUMN does not fire the append-only UPDATE triggers of the paper tables.
ALTER TABLE audit_log ADD COLUMN hash_alg TEXT NOT NULL DEFAULT 'sha256';
ALTER TABLE paper_ledger ADD COLUMN hash_alg TEXT NOT NULL DEFAULT 'sha256';
ALTER TABLE paper_days ADD COLUMN hash_alg TEXT NOT NULL DEFAULT 'sha256';

-- How many unkeyed rows each chain had when this migration ran. Only that prefix may be 'sha256'.
-- The application seals each row with an HMAC (mac) right after the migration; an unsealed or
-- mis-sealed row makes verification fail.
CREATE TABLE chain_epoch (
    chain        TEXT PRIMARY KEY,
    legacy_rows  INTEGER NOT NULL,
    mac          TEXT
);
INSERT INTO chain_epoch(chain, legacy_rows) SELECT 'audit', COUNT(*) FROM audit_log;
INSERT INTO chain_epoch(chain, legacy_rows)
    SELECT 'paper_ledger:' || book, COUNT(*) FROM paper_ledger GROUP BY book;
INSERT INTO chain_epoch(chain, legacy_rows)
    SELECT 'paper_days:' || book, COUNT(*) FROM paper_days GROUP BY book;
CREATE TRIGGER chain_epoch_no_delete BEFORE DELETE ON chain_epoch
BEGIN SELECT RAISE(ABORT, 'chain_epoch rows are permanent'); END;
CREATE TRIGGER chain_epoch_no_insert_late BEFORE INSERT ON chain_epoch
BEGIN SELECT RAISE(ABORT, 'chain_epoch is written only by migration 0004'); END;

-- Kill switch: an append-only log of set and clear events; the latest row is the state.
CREATE TABLE kill_switch_events (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL CHECK (action IN ('set', 'clear')),
    reason  TEXT NOT NULL
);
CREATE TRIGGER kill_switch_events_no_update BEFORE UPDATE ON kill_switch_events
BEGIN SELECT RAISE(ABORT, 'kill_switch_events is append-only'); END;
CREATE TRIGGER kill_switch_events_no_delete BEFORE DELETE ON kill_switch_events
BEGIN SELECT RAISE(ABORT, 'kill_switch_events is append-only'); END;
