"""Protect history-to-idempotency linkage without an initial scan."""

from alembic import op

revision = "0008_event_fk"
down_revision = "0007_class_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ticket_assignment_history
        ADD CONSTRAINT fk_ticket_assignment_history_event_key
        FOREIGN KEY (event_key)
        REFERENCES ticket_assignment_event_keys(event_key)
        ON DELETE RESTRICT
        NOT VALID
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ticket_assignment_history "
        "DROP CONSTRAINT fk_ticket_assignment_history_event_key"
    )
