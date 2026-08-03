"""Validate the data quality checks added without a table rewrite."""

from alembic import op

revision = "0003_validate_checks"
down_revision = "0002_quality_checks"
branch_labels = None
depends_on = None

CHECK_NAMES = {
    "ia_classifications": (
        "ck_ia_classifications_confidence_range",
        "ck_ia_classifications_message_count_nonnegative",
        "ck_ia_classifications_processing_time_nonnegative",
        "ck_ia_classifications_message_ids_array",
        "ck_ia_classifications_department_array",
        "ck_ia_classifications_agent_array",
        "ck_ia_classifications_updated_at_present",
        "ck_ia_classifications_timestamp_order",
        "ck_ia_classifications_review_timestamp_order",
        "ck_ia_classifications_conversation_nonblank",
        "ck_ia_classifications_model_nonblank",
    ),
    "message_transcriptions": (
        "ck_message_transcriptions_message_nonblank",
        "ck_message_transcriptions_model_nonblank",
        "ck_message_transcriptions_attempt_nonnegative",
        "ck_message_transcriptions_timestamp_order",
        "ck_message_transcriptions_completion",
    ),
    "message_image_extractions": (
        "ck_message_image_extractions_message_nonblank",
        "ck_message_image_extractions_model_nonblank",
        "ck_message_image_extractions_attempt_nonnegative",
        "ck_message_image_extractions_timestamp_order",
        "ck_message_image_extractions_completion",
    ),
    "ticket_assignment_history": (
        "ck_ticket_assignment_history_conversation_nonblank",
        "ck_ticket_assignment_history_event_key_nonblank",
        "ck_ticket_assignment_history_transfer_count_nonnegative",
    ),
    "ticket_assignment_event_keys": (
        "ck_ticket_assignment_event_keys_key_nonblank",
        "ck_ticket_assignment_event_keys_conversation_nonblank",
    ),
    "digisac_departments": (
        "ck_digisac_departments_id_nonblank",
        "ck_digisac_departments_name_nonblank",
    ),
    "digisac_users": (
        "ck_digisac_users_id_nonblank",
        "ck_digisac_users_name_nonblank",
    ),
    "digisac_directory_sync_state": ("ck_digisac_directory_sync_state_resource",),
}


def upgrade() -> None:
    for table, names in CHECK_NAMES.items():
        for name in names:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def downgrade() -> None:
    # A validated CHECK has the same semantics as its NOT VALID predecessor for
    # new rows. PostgreSQL cannot mark it unvalidated, so schema remains safe.
    pass
