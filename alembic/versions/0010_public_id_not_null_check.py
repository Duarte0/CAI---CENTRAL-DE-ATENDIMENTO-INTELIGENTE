"""Add a deferred public-id completeness check."""

from alembic import op

revision = "0010_public_id_check"
down_revision = "0009_recovery_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications ADD CONSTRAINT "
        "ck_ia_classifications_public_id_present "
        "CHECK (public_id IS NOT NULL) NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications "
        "DROP CONSTRAINT ck_ia_classifications_public_id_present"
    )
