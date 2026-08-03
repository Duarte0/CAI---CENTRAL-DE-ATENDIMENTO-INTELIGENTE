"""Add nullable public and idempotency identifiers."""

from alembic import op

revision = "0005_class_identity"
down_revision = "0004_updated_at_nn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ia_classifications ADD COLUMN public_id UUID")
    op.execute("ALTER TABLE ia_classifications ADD COLUMN idempotency_key TEXT")
    op.execute(
        "ALTER TABLE ia_classifications ADD CONSTRAINT "
        "ck_ia_classifications_idempotency_key_nonblank "
        "CHECK (idempotency_key IS NULL OR btrim(idempotency_key) <> '') NOT VALID"
    )


def downgrade() -> None:
    populated = (
        op.get_bind()
        .exec_driver_sql(
            """
        SELECT COUNT(*)
        FROM ia_classifications
        WHERE public_id IS NOT NULL OR idempotency_key IS NOT NULL
        """
        )
        .scalar_one()
    )
    if populated:
        raise RuntimeError(
            "classification identity columns contain data; "
            "refusing a data-losing downgrade"
        )
    op.execute(
        "ALTER TABLE ia_classifications "
        "DROP CONSTRAINT ck_ia_classifications_idempotency_key_nonblank"
    )
    op.execute(
        "ALTER TABLE ia_classifications DROP COLUMN idempotency_key, "
        "DROP COLUMN public_id"
    )
