-- Application startup executes equivalent guarded changes idempotently.
ALTER TABLE ia_classifications
    ADD COLUMN department TEXT NOT NULL DEFAULT '[]';
ALTER TABLE ia_classifications
    ADD COLUMN agent TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS ticket_assignment_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    department_id TEXT,
    user_id TEXT,
    event_timestamp TEXT NOT NULL,
    source_event_id TEXT,
    event_key TEXT NOT NULL UNIQUE,
    ticket_transfer_count INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticket_assignment_history_conversation_time
    ON ticket_assignment_history(conversation_id, event_timestamp, id);
CREATE TABLE IF NOT EXISTS ticket_assignment_event_keys (
    event_key TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digisac_departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_updated_at TEXT,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digisac_users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_updated_at TEXT,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digisac_directory_sync_state (
    resource TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT
);
