-- v6 (0.10.0, X3 / finding R-21): the highest schema version this file has reached, sealed with the
-- chain key. Setting PRAGMA user_version back below v4 used to make every row count as legacy, and
-- the next open re-ran 0004 and re-sealed forged rows with the real key. The application writes the
-- seal right after migrating; a user_version below the sealed version fails verification.
CREATE TABLE schema_seal (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    version  INTEGER NOT NULL,
    mac      TEXT NOT NULL
);
CREATE TRIGGER schema_seal_no_delete BEFORE DELETE ON schema_seal
BEGIN SELECT RAISE(ABORT, 'schema_seal is permanent'); END;
