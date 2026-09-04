"""Add explicit leases for durable media polling workers."""

from alembic import op


revision = "0024_durable_media_leases"
down_revision = "0023_manual_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_transcriptions
            ADD COLUMN lease_owner TEXT,
            ADD COLUMN lease_expires_at TIMESTAMPTZ;
        ALTER TABLE message_image_extractions
            ADD COLUMN lease_owner TEXT,
            ADD COLUMN lease_expires_at TIMESTAMPTZ;

        -- Rows created before explicit leases have no owner. Their previous
        -- updated_at is the only safe evidence available for recovery.
        UPDATE message_transcriptions
        SET lease_expires_at = updated_at,
            enqueued_at = NULL
        WHERE status = 'processing' AND lease_expires_at IS NULL;
        UPDATE message_transcriptions
        SET enqueued_at = NULL
        WHERE status <> 'processing';
        UPDATE message_image_extractions
        SET lease_expires_at = updated_at
        WHERE status = 'processing' AND lease_expires_at IS NULL;
        """
    )
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_message_transcriptions_polling
            ON message_transcriptions (
                next_attempt_at, lease_expires_at, updated_at, message_id
            )
            WHERE status IN ('pending', 'processing')
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY idx_message_image_extractions_polling
            ON message_image_extractions (
                next_attempt_at, lease_expires_at, updated_at, message_id
            )
            WHERE status IN ('pending', 'processing')
            """
        )


def downgrade() -> None:
    connection = op.get_bind()
    active = connection.exec_driver_sql(
        """
        SELECT EXISTS (
            SELECT 1 FROM message_transcriptions
            WHERE lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL
        ) OR EXISTS (
            SELECT 1 FROM message_image_extractions
            WHERE lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL
        )
        """
    ).scalar_one()
    if active:
        raise RuntimeError(
            "durable media leases are active; refusing a data-losing downgrade"
        )
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_message_image_extractions_polling"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "idx_message_transcriptions_polling"
        )
    op.execute(
        """
        ALTER TABLE message_image_extractions
            DROP COLUMN lease_expires_at,
            DROP COLUMN lease_owner;
        ALTER TABLE message_transcriptions
            DROP COLUMN lease_expires_at,
            DROP COLUMN lease_owner;
        """
    )
