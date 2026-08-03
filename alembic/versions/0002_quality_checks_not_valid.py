"""Add low-lock data quality checks without scanning existing rows."""

from alembic import op

revision = "0002_quality_checks"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


CHECKS = {
    "ia_classifications": {
        "ck_ia_classifications_confidence_range": "confidence IS NULL OR confidence BETWEEN 0 AND 1",
        "ck_ia_classifications_message_count_nonnegative": "message_count >= 0",
        "ck_ia_classifications_processing_time_nonnegative": "processing_time_ms >= 0",
        "ck_ia_classifications_message_ids_array": "jsonb_typeof(message_ids) = 'array'",
        "ck_ia_classifications_department_array": "jsonb_typeof(department) = 'array'",
        "ck_ia_classifications_agent_array": "jsonb_typeof(agent) = 'array'",
        "ck_ia_classifications_updated_at_present": "updated_at IS NOT NULL",
        "ck_ia_classifications_timestamp_order": "updated_at IS NULL OR updated_at >= created_at",
        "ck_ia_classifications_review_timestamp_order": "reviewed_at IS NULL OR reviewed_at >= created_at",
        "ck_ia_classifications_conversation_nonblank": "btrim(conversation_id) <> ''",
        "ck_ia_classifications_model_nonblank": "btrim(model) <> ''",
    },
    "message_transcriptions": {
        "ck_message_transcriptions_message_nonblank": "btrim(message_id) <> ''",
        "ck_message_transcriptions_model_nonblank": "btrim(model) <> ''",
        "ck_message_transcriptions_attempt_nonnegative": "attempt_count >= 0",
        "ck_message_transcriptions_timestamp_order": "updated_at >= created_at",
        "ck_message_transcriptions_completion": "(status = 'completed' AND completed_at IS NOT NULL "
        "AND text IS NOT NULL AND btrim(text) <> '') "
        "OR (status <> 'completed' AND completed_at IS NULL)",
    },
    "message_image_extractions": {
        "ck_message_image_extractions_message_nonblank": "btrim(message_id) <> ''",
        "ck_message_image_extractions_model_nonblank": "btrim(model) <> ''",
        "ck_message_image_extractions_attempt_nonnegative": "attempt_count >= 0",
        "ck_message_image_extractions_timestamp_order": "updated_at >= created_at",
        "ck_message_image_extractions_completion": "(status = 'completed' AND completed_at IS NOT NULL "
        "AND text IS NOT NULL AND btrim(text) <> '') "
        "OR (status <> 'completed' AND completed_at IS NULL)",
    },
    "ticket_assignment_history": {
        "ck_ticket_assignment_history_conversation_nonblank": "btrim(conversation_id) <> ''",
        "ck_ticket_assignment_history_event_key_nonblank": "btrim(event_key) <> ''",
        "ck_ticket_assignment_history_transfer_count_nonnegative": "ticket_transfer_count IS NULL OR ticket_transfer_count >= 0",
    },
    "ticket_assignment_event_keys": {
        "ck_ticket_assignment_event_keys_key_nonblank": "btrim(event_key) <> ''",
        "ck_ticket_assignment_event_keys_conversation_nonblank": "btrim(conversation_id) <> ''",
    },
    "digisac_departments": {
        "ck_digisac_departments_id_nonblank": "btrim(id) <> ''",
        "ck_digisac_departments_name_nonblank": "btrim(name) <> ''",
    },
    "digisac_users": {
        "ck_digisac_users_id_nonblank": "btrim(id) <> ''",
        "ck_digisac_users_name_nonblank": "btrim(name) <> ''",
    },
    "digisac_directory_sync_state": {
        "ck_digisac_directory_sync_state_resource": "resource IN ('departments', 'users')",
    },
}


def upgrade() -> None:
    for table, checks in CHECKS.items():
        for name, expression in checks.items():
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {name} "
                f"CHECK ({expression}) NOT VALID"
            )


def downgrade() -> None:
    for table, checks in reversed(list(CHECKS.items())):
        for name in reversed(list(checks)):
            op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
