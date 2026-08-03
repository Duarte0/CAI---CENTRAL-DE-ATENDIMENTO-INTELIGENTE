"""Validate and finalize the public UUID without rewriting rows."""

from alembic import op

revision = "0011_public_id_final"
down_revision = "0010_public_id_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications "
        "VALIDATE CONSTRAINT ck_ia_classifications_public_id_present"
    )
    op.execute("ALTER TABLE ia_classifications ALTER COLUMN public_id SET NOT NULL")
    op.execute(
        "ALTER TABLE ia_classifications "
        "ADD CONSTRAINT uq_ia_classifications_public_id "
        "UNIQUE USING INDEX ux_ia_classifications_public_id"
    )
    op.execute(
        "ALTER TABLE ia_classifications "
        "DROP CONSTRAINT ck_ia_classifications_public_id_present"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications "
        "ADD CONSTRAINT ck_ia_classifications_public_id_present "
        "CHECK (public_id IS NOT NULL) NOT VALID"
    )
    op.execute(
        "ALTER TABLE ia_classifications "
        "VALIDATE CONSTRAINT ck_ia_classifications_public_id_present"
    )
    op.execute(
        "ALTER TABLE ia_classifications "
        "DROP CONSTRAINT uq_ia_classifications_public_id"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_ia_classifications_public_id "
        "ON ia_classifications(public_id)"
    )
    op.execute("ALTER TABLE ia_classifications ALTER COLUMN public_id DROP NOT NULL")
