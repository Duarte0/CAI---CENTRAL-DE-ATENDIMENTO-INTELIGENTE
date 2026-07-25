CREATE TABLE IF NOT EXISTS message_transcriptions (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    text TEXT,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_message_transcriptions_conversation_id
    ON message_transcriptions(conversation_id);
CREATE INDEX IF NOT EXISTS idx_message_transcriptions_status
    ON message_transcriptions(status);
