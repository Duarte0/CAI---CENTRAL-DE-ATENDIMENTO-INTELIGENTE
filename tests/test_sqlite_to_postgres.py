import json
import sqlite3

import pytest

from src.utils.migrate_sqlite_to_postgres import (
    MigrationReport,
    _json_value,
    _sqlite_rows,
    _timestamp,
)


def test_json_conversion_preserves_structured_lists():
    value = _json_value(
        "ia_classifications", "department", json.dumps(["Atendimento", "T.I."])
    )
    assert value is not None
    assert value.obj == ["Atendimento", "T.I."]


def test_json_conversion_rejects_non_list_assignment():
    with pytest.raises(ValueError, match="JSON list"):
        _json_value("ia_classifications", "agent", json.dumps("Ana"))


def test_naive_timestamp_is_normalized_and_reported():
    report = MigrationReport()
    value = _timestamp("2026-07-24T10:00:00", report)
    assert value is not None
    assert value.tzinfo is not None
    assert value.isoformat() == "2026-07-24T10:00:00+00:00"
    assert report.normalized_naive_timestamps == 1


def test_sqlite_source_is_opened_read_only(tmp_path):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE ia_classifications (
                id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL,
                message_ids TEXT NOT NULL, created_at TEXT NOT NULL,
                full_context TEXT NOT NULL, message_count INTEGER NOT NULL,
                intent_type TEXT NOT NULL, confidence REAL, title TEXT,
                description TEXT, department TEXT NOT NULL, agent TEXT NOT NULL,
                model TEXT NOT NULL, processing_time_ms INTEGER NOT NULL,
                prompt_version TEXT, reviewed_at TEXT, reviewed_by TEXT,
                corrected_intent_type TEXT, corrected_title TEXT,
                corrected_description TEXT, updated_at TEXT, protocol TEXT
            );
            """
        )
        for table in (
            "message_transcriptions", "message_image_extractions",
            "ticket_assignment_history", "ticket_assignment_event_keys",
            "digisac_departments", "digisac_users",
            "digisac_directory_sync_state",
        ):
            if table == "message_transcriptions" or table == "message_image_extractions":
                connection.execute(
                    f"CREATE TABLE {table} (message_id TEXT PRIMARY KEY, conversation_id TEXT, text TEXT, model TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL, error_message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT)"
                )
            elif table == "ticket_assignment_history":
                connection.execute(
                    "CREATE TABLE ticket_assignment_history (id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, department_id TEXT, user_id TEXT, event_timestamp TEXT NOT NULL, source_event_id TEXT, event_key TEXT NOT NULL, ticket_transfer_count INTEGER, created_at TEXT NOT NULL)"
                )
            elif table == "ticket_assignment_event_keys":
                connection.execute("CREATE TABLE ticket_assignment_event_keys (event_key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, created_at TEXT NOT NULL)")
            elif table in {"digisac_departments", "digisac_users"}:
                connection.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, name TEXT NOT NULL, source_updated_at TEXT, synced_at TEXT NOT NULL)")
            else:
                connection.execute("CREATE TABLE digisac_directory_sync_state (resource TEXT PRIMARY KEY, last_attempt_at TEXT, last_success_at TEXT)")
        connection.execute(
            "INSERT INTO ia_classifications VALUES (1, 'c', '[]', '2026-07-24T10:00:00+00:00', 'x', 1, 'question', NULL, NULL, NULL, '[]', '[]', 'm', 1, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
        )
    rows = _sqlite_rows(path)
    assert len(rows["ia_classifications"]) == 1
    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as connection:
            connection.execute("DELETE FROM ia_classifications")
