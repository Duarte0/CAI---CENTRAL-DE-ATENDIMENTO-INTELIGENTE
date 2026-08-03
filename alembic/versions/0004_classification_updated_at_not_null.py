"""Promote ia_classifications.updated_at to NOT NULL."""

from alembic import op

revision = "0004_updated_at_nn"
down_revision = "0003_validate_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ia_classifications ALTER COLUMN updated_at SET NOT NULL")
    op.execute(
        "ALTER TABLE ia_classifications "
        "DROP CONSTRAINT ck_ia_classifications_updated_at_present"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications "
        "ADD CONSTRAINT ck_ia_classifications_updated_at_present "
        "CHECK (updated_at IS NOT NULL) NOT VALID"
    )
    op.execute(
        "ALTER TABLE ia_classifications "
        "VALIDATE CONSTRAINT ck_ia_classifications_updated_at_present"
    )
    op.execute("ALTER TABLE ia_classifications ALTER COLUMN updated_at DROP NOT NULL")
