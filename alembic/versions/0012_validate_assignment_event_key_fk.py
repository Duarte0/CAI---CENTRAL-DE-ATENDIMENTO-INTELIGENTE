"""Validate the assignment-history idempotency foreign key."""

from alembic import op

revision = "0012_validate_event_fk"
down_revision = "0011_public_id_final"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ticket_assignment_history "
        "VALIDATE CONSTRAINT fk_ticket_assignment_history_event_key"
    )


def downgrade() -> None:
    # PostgreSQL cannot revert a validated FK to NOT VALID. Keeping it
    # validated preserves the exact write semantics of revision 0008.
    pass
