"""Persist durable media schedules and blocked finalization state."""

from alembic import op


revision = "0014_retry_scheduling"
down_revision = "0013_conversation_cycles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_transcriptions
            ADD COLUMN next_attempt_at TIMESTAMPTZ,
            ADD COLUMN enqueued_at TIMESTAMPTZ;
        ALTER TABLE message_image_extractions
            ADD COLUMN next_attempt_at TIMESTAMPTZ,
            ADD COLUMN enqueued_at TIMESTAMPTZ;
        ALTER TABLE conversation_processing_cycles
            ADD COLUMN transient_retry_count INTEGER NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_conversation_cycles_transient_retry_nonnegative
                CHECK (transient_retry_count >= 0) NOT VALID,
            ADD CONSTRAINT ck_conversation_cycles_status_v2 CHECK (status IN (
                'open', 'pending', 'recovering_messages', 'waiting_media',
                'media_blocked', 'building_context', 'summarizing',
                'classifying', 'completed', 'completed_with_warnings',
                'retryable_failure', 'failed'
            )) NOT VALID;

        ALTER TABLE conversation_processing_cycles
            VALIDATE CONSTRAINT ck_conversation_cycles_transient_retry_nonnegative;
        ALTER TABLE conversation_processing_cycles
            VALIDATE CONSTRAINT ck_conversation_cycles_status_v2;
        ALTER TABLE conversation_processing_cycles
            DROP CONSTRAINT ck_conversation_cycles_status;
        ALTER TABLE conversation_processing_cycles
            RENAME CONSTRAINT ck_conversation_cycles_status_v2
            TO ck_conversation_cycles_status;
        """
    )
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_message_transcriptions_schedule
            ON message_transcriptions (
                next_attempt_at, enqueued_at, updated_at, message_id
            )
            WHERE status = 'pending'
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_message_image_extractions_schedule
            ON message_image_extractions (
                next_attempt_at, enqueued_at, updated_at, message_id
            )
            WHERE status = 'pending'
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_conversation_cycles_media_blocked
            ON conversation_processing_cycles (id)
            WHERE status = 'media_blocked'
            """
        )


def downgrade() -> None:
    connection = op.get_bind()
    active = connection.exec_driver_sql(
        """
        SELECT
            EXISTS (
                SELECT 1
                FROM conversation_processing_cycles
                WHERE status = 'media_blocked'
                   OR transient_retry_count > 0
            )
            OR EXISTS (
                SELECT 1 FROM message_transcriptions
                WHERE next_attempt_at IS NOT NULL OR enqueued_at IS NOT NULL
            )
            OR EXISTS (
                SELECT 1 FROM message_image_extractions
                WHERE next_attempt_at IS NOT NULL OR enqueued_at IS NOT NULL
            )
        """
    ).scalar_one()
    if active:
        raise RuntimeError(
            "durable retry state is active; refusing a data-losing downgrade"
        )
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_conversation_cycles_media_blocked"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_message_image_extractions_schedule"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_message_transcriptions_schedule"
        )
    op.execute(
        """
        ALTER TABLE conversation_processing_cycles
            ADD CONSTRAINT ck_conversation_cycles_status_v1 CHECK (status IN (
                'open', 'pending', 'recovering_messages', 'waiting_media',
                'building_context', 'summarizing', 'classifying',
                'completed', 'completed_with_warnings',
                'retryable_failure', 'failed'
            )) NOT VALID;
        ALTER TABLE conversation_processing_cycles
            VALIDATE CONSTRAINT ck_conversation_cycles_status_v1;
        ALTER TABLE conversation_processing_cycles
            DROP CONSTRAINT ck_conversation_cycles_status,
            DROP CONSTRAINT ck_conversation_cycles_transient_retry_nonnegative,
            DROP COLUMN transient_retry_count;
        ALTER TABLE conversation_processing_cycles
            RENAME CONSTRAINT ck_conversation_cycles_status_v1
            TO ck_conversation_cycles_status;
        ALTER TABLE message_image_extractions
            DROP COLUMN enqueued_at,
            DROP COLUMN next_attempt_at;
        ALTER TABLE message_transcriptions
            DROP COLUMN enqueued_at,
            DROP COLUMN next_attempt_at;
        """
    )
