"""Create public-id and idempotency uniqueness indexes online."""

from alembic import op

revision = "0006_identity_indexes"
down_revision = "0005_class_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ia_classifications "
        "VALIDATE CONSTRAINT ck_ia_classifications_idempotency_key_nonblank"
    )
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "ux_ia_classifications_public_id "
            "ON ia_classifications (public_id)"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY "
            "ux_ia_classifications_idempotency_key "
            "ON ia_classifications (idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )


def downgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS " "ux_ia_classifications_idempotency_key"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS " "ux_ia_classifications_public_id"
        )
