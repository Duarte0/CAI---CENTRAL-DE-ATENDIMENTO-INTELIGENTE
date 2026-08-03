"""Add online partial indexes for stale media-work recovery."""

from alembic import op

revision = "0009_recovery_indexes"
down_revision = "0008_event_fk"
branch_labels = None
depends_on = None


INDEXES = {
    "idx_message_transcriptions_recovery": "message_transcriptions (updated_at, message_id)",
    "idx_message_image_extractions_recovery": "message_image_extractions (updated_at, message_id)",
}


def upgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        for name, target in INDEXES.items():
            op.execute(
                f"CREATE INDEX CONCURRENTLY {name} ON {target} "
                "WHERE status IN ('pending', 'processing')"
            )


def downgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        for name in reversed(list(INDEXES)):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
